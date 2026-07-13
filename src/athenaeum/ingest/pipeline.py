"""Orchestrate the full ingestion pipeline for a single paper."""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import httpx
import pysbd
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.config import Settings
from athenaeum.ingest.acquire import acquire_pdf
from athenaeum.ingest.embed import embed_sentences
from athenaeum.ingest.grobid import Section, parse_pdf
from athenaeum.ingest.htmlextract import extract_main_text
from athenaeum.ingest.zotero import (
    ZoteroAuthError,
    ZoteroCreds,
    download_attachment,
    list_attachments,
    pick_best_attachment,
)
from athenaeum.ingest.zotero_local import (
    LocalZoteroError,
    list_local_attachments,
    resolve_configured_data_dir,
    resolve_local_attachment_file,
)
from athenaeum.storage.models import Paper, PaperSection, PaperSentence

log = structlog.get_logger()

_seg = pysbd.Segmenter(language="en", clean=False, char_span=True)


async def run_ingest(
    paper_id: uuid.UUID,
    oa_pdf_url: str | None,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    settings: Settings,
    s3_key: str | None = None,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            log.warning("ingest.paper_not_found", paper_id=str(paper_id))
            return

        try:
            await _process(paper, oa_pdf_url, session, http, settings, s3_key)
        except Exception:
            log.exception("ingest.failed", paper_id=str(paper_id))
            paper.status = "failed"
            await session.commit()
            raise


def _sections_from_text(text: str) -> list[Section]:
    """Split plain text into sections by double newlines."""
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if not blocks:
        return []
    return [Section(label="Full Text", text="\n\n".join(blocks))]


async def _process(
    paper: Paper,
    oa_pdf_url: str | None,
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
    s3_key: str | None = None,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)

        if s3_key:
            if not settings.s3_bucket:
                raise RuntimeError("S3_BUCKET not configured but s3_key was provided")
            from athenaeum.storage.s3 import delete_object, download_to_path

            dest = tmp_dir / Path(s3_key).name
            log.info("ingest.s3_download", paper_id=str(paper.id), key=s3_key)
            await download_to_path(
                settings.s3_bucket, s3_key, dest, settings.aws_region, settings.s3_endpoint_url
            )
            await delete_object(
                settings.s3_bucket, s3_key, settings.aws_region, settings.s3_endpoint_url
            )

            if dest.suffix.lower() == ".txt":
                sections = _sections_from_text(dest.read_text(errors="replace"))
            else:
                log.info("ingest.grobid", paper_id=str(paper.id))
                sections = await parse_pdf(dest, settings.grobid_url, http)
        else:
            log.info("ingest.acquire_pdf", paper_id=str(paper.id), doi=paper.doi)
            pdf_path = await acquire_pdf(
                doi=paper.doi,
                oa_url=oa_pdf_url,
                client=http,
                tmp_dir=tmp_dir,
                unpaywall_email=settings.unpaywall_email,
            )

            if pdf_path is None:
                log.warning("ingest.no_pdf", paper_id=str(paper.id))
                paper.status = "failed"
                await session.commit()
                return

            log.info("ingest.grobid", paper_id=str(paper.id))
            sections = await parse_pdf(pdf_path, settings.grobid_url, http)

    await _finalize_sections(paper, sections, session, settings)


async def _finalize_sections(
    paper: Paper,
    sections: list[Section],
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Given parsed sections, insert PaperSection/PaperSentence rows with embeddings
    and mark the paper full_text. Shared tail for every acquisition path (DOI/upload
    PDF, plain-text upload, Zotero PDF/HTML snapshot)."""
    if not sections:
        log.warning("ingest.no_sections", paper_id=str(paper.id))
        paper.status = "failed"
        await session.commit()
        return

    global_char_offset = 0
    global_sentence_ord = 0

    for section_ord, section in enumerate(sections):
        sec_start = global_char_offset
        sec_end = sec_start + len(section.text)

        db_section = PaperSection(
            id=uuid.uuid4(),
            paper_id=paper.id,
            label=section.label,
            ord=section_ord,
            char_start=sec_start,
            char_end=sec_end,
        )
        session.add(db_section)
        await session.flush()  # populate db_section.id

        # Sentence-tokenize within section text
        spans = _seg.segment(section.text)
        sentence_texts = [sp.sent for sp in spans]

        if not sentence_texts:
            global_char_offset = sec_end + 2  # "\n\n" separator
            continue

        log.debug(
            "ingest.embed",
            paper_id=str(paper.id),
            section=section.label,
            n_sentences=len(sentence_texts),
        )
        embeddings = await embed_sentences(sentence_texts, settings.embed_model)

        for span, embedding in zip(spans, embeddings):
            db_sent = PaperSentence(
                id=uuid.uuid4(),
                paper_id=paper.id,
                section_id=db_section.id,
                ord=global_sentence_ord,
                text=span.sent,
                char_start=sec_start + span.start,
                char_end=sec_start + span.end,
                embedding=embedding,
            )
            session.add(db_sent)
            global_sentence_ord += 1

        global_char_offset = sec_end + 2  # account for "\n\n" between sections

    paper.status = "full_text"
    await session.commit()
    log.info(
        "ingest.done",
        paper_id=str(paper.id),
        sections=len(sections),
        sentences=global_sentence_ord,
    )


# ── Zotero ingestion ────────────────────────────────────────────────────────────

async def run_zotero_ingest(
    paper_id: uuid.UUID,
    zotero_item_key: str,
    creds: ZoteroCreds,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """Mirrors run_ingest()'s shape, but acquires the paper's content from Zotero
    instead of DOI/Unpaywall/S2 or a staged S3 upload."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            log.warning("ingest.paper_not_found", paper_id=str(paper_id))
            return

        try:
            await _process_zotero(paper, zotero_item_key, creds, session, http, settings)
        except ZoteroAuthError:
            # Revoked/invalid key — don't let arq burn retries on a call that will
            # never succeed until the user reconnects.
            log.warning("ingest.zotero_auth_failed", paper_id=str(paper_id))
            paper.status = "failed"
            await session.commit()
        except Exception:
            log.exception("ingest.zotero_failed", paper_id=str(paper_id))
            paper.status = "failed"
            await session.commit()
            raise


async def _process_zotero(
    paper: Paper,
    zotero_item_key: str,
    creds: ZoteroCreds,
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
) -> None:
    log.info("ingest.zotero_list_attachments", paper_id=str(paper.id))
    attachments = await list_attachments(creds, zotero_item_key)
    best = pick_best_attachment(attachments)

    if best is None:
        # No downloadable attachment (metadata-only item, or only linked_file/
        # linked_url attachments pointing somewhere the server can't reach) —
        # same soft-failure semantics as "no PDF acquirable" in the DOI path.
        log.warning("ingest.zotero_no_attachment", paper_id=str(paper.id))
        paper.status = "failed"
        await session.commit()
        return

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        is_pdf = best.content_type == "application/pdf"
        dest = tmp_dir / ("attachment.pdf" if is_pdf else "attachment.html")

        log.info(
            "ingest.zotero_download",
            paper_id=str(paper.id),
            content_type=best.content_type,
        )
        await download_attachment(creds, best, http, dest, settings.zotero_api_base)

        if is_pdf:
            log.info("ingest.grobid", paper_id=str(paper.id))
            sections = await parse_pdf(dest, settings.grobid_url, http)
        else:
            text = extract_main_text(dest.read_text(errors="replace"))
            sections = _sections_from_text(text) if text else []

    await _finalize_sections(paper, sections, session, settings)


# ── Local Zotero ingestion ──────────────────────────────────────────────────────

async def run_local_zotero_ingest(
    paper_id: uuid.UUID,
    zotero_item_key: str,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """Mirrors run_zotero_ingest()'s shape, but reads the attachment straight off
    disk from a bind-mounted local Zotero data directory instead of the Web API."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            log.warning("ingest.paper_not_found", paper_id=str(paper_id))
            return

        try:
            await _process_local_zotero(paper, zotero_item_key, session, http, settings)
        except LocalZoteroError as exc:
            log.warning("ingest.local_zotero_unavailable", paper_id=str(paper_id), error=str(exc))
            paper.status = "failed"
            await session.commit()
        except Exception:
            log.exception("ingest.local_zotero_failed", paper_id=str(paper_id))
            paper.status = "failed"
            await session.commit()
            raise


async def _process_local_zotero(
    paper: Paper,
    zotero_item_key: str,
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
) -> None:
    data_dir = await resolve_configured_data_dir(session, settings.zotero_local_host_mount)

    log.info("ingest.local_zotero_list_attachments", paper_id=str(paper.id))
    attachments = await list_local_attachments(data_dir, zotero_item_key)
    best = pick_best_attachment(attachments)

    if best is None:
        log.warning("ingest.local_zotero_no_attachment", paper_id=str(paper.id))
        paper.status = "failed"
        await session.commit()
        return

    source_path = resolve_local_attachment_file(data_dir, best)
    if source_path is None:
        log.warning("ingest.local_zotero_file_missing", paper_id=str(paper.id))
        paper.status = "failed"
        await session.commit()
        return

    is_pdf = best.content_type == "application/pdf"
    if is_pdf:
        log.info("ingest.grobid", paper_id=str(paper.id))
        sections = await parse_pdf(source_path, settings.grobid_url, http)
    else:
        text = extract_main_text(source_path.read_text(errors="replace"))
        sections = _sections_from_text(text) if text else []

    await _finalize_sections(paper, sections, session, settings)
