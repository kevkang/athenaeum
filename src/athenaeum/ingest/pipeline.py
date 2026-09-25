"""Orchestrate the full ingestion pipeline for a single paper."""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

import httpx
import pysbd
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.config import Settings
from athenaeum.domain.source_check import recheck_paper
from athenaeum.ingest.acquire import acquire_pdf
from athenaeum.ingest.embed import embed_sentences
from athenaeum.ingest.grobid import Section, parse_pdf
from athenaeum.ingest.htmlextract import extract_main_text
from athenaeum.ingest.openalex import OpenAlexMeta, fetch_metadata
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
from athenaeum.storage.models import Paper, PaperSection, PaperSentence, ProjectPaper

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

        # strict=True: spans and embeddings must be 1:1 by construction (one
        # embedding per sentence_texts entry) — a length mismatch would mean
        # embeddings got silently misaligned to the wrong sentences, which
        # should fail loudly rather than zip to the shorter length.
        for span, embedding in zip(spans, embeddings, strict=True):
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

    # This server now has the paper's text, so check every passage quoting it
    # against that text, including quotes imported while the paper was missing.
    await session.flush()
    recheck = await recheck_paper(session, paper.id)
    if recheck:
        log.info("ingest.source_recheck", paper_id=str(paper.id), **recheck)

    paper.status = "full_text"
    if paper.doi and not paper.openalex_id:
        await backfill_openalex_id(paper, session)
    await session.commit()
    log.info(
        "ingest.done",
        paper_id=str(paper.id),
        sections=len(sections),
        sentences=global_sentence_ord,
    )


async def backfill_openalex_id(paper: Paper, session: AsyncSession) -> bool:
    """Resolve and assign an openalex_id from a paper's own DOI when it's
    missing — e.g. Zotero-imported papers never get one at import time (Zotero
    itself has no OpenAlex link), and a DOI lookup can fail transiently at
    initial ingest. Skips assignment (rather than crashing on the unique
    constraint) if another paper already claims that openalex_id. Returns
    whether an id was assigned."""
    if not paper.doi:
        return False
    loop = asyncio.get_event_loop()
    try:
        meta = await loop.run_in_executor(None, fetch_metadata, paper.doi)
    except Exception:
        log.warning("ingest.openalex_backfill_failed", paper_id=str(paper.id))
        return False
    if not meta.openalex_id:
        return False

    existing = await session.scalar(select(Paper).where(Paper.openalex_id == meta.openalex_id))
    if existing is not None and existing.id != paper.id:
        log.info(
            "ingest.openalex_backfill_duplicate",
            paper_id=str(paper.id), existing_paper_id=str(existing.id),
        )
        return False

    paper.openalex_id = meta.openalex_id
    if not paper.abstract and meta.abstract:
        paper.abstract = meta.abstract
    if not paper.venue and meta.venue:
        paper.venue = meta.venue
    if not paper.year and meta.year:
        paper.year = meta.year
    log.info("ingest.openalex_backfilled", paper_id=str(paper.id), openalex_id=meta.openalex_id)
    return True


async def find_or_create_target_paper(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID, meta: OpenAlexMeta
) -> Paper:
    """Resolve `meta` to a real Paper row in this project — reusing an
    existing paper (matched by DOI, then OpenAlex id) if one already exists
    anywhere in the DB, linking it into the project if it isn't already, or
    else creating a new ghost (not-yet-ingested) Paper. Shared by live
    backtrace hop resolution and .athtrace import, both of which need to
    point a hop at whatever UUID a cited paper already has in *this*
    database rather than assuming a fresh id."""
    existing = None
    if meta.doi:
        existing = await session.scalar(select(Paper).where(Paper.doi == meta.doi))
    if existing is None and meta.openalex_id:
        existing = await session.scalar(select(Paper).where(Paper.openalex_id == meta.openalex_id))

    if existing is not None:
        already_linked = await session.scalar(
            select(ProjectPaper).where(
                ProjectPaper.project_id == project_id, ProjectPaper.paper_id == existing.id
            )
        )
        if already_linked is None:
            session.add(ProjectPaper(project_id=project_id, paper_id=existing.id, added_by=user_id))
            await session.flush()
        return existing

    paper = Paper(
        id=uuid.uuid4(),
        doi=meta.doi,
        openalex_id=meta.openalex_id,
        title=meta.title,
        authors_json=meta.authors,
        venue=meta.venue,
        year=meta.year,
        abstract=meta.abstract,
        status="ghost",
    )
    session.add(paper)
    session.add(ProjectPaper(project_id=project_id, paper_id=paper.id, added_by=user_id))
    await session.flush()
    return paper


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
