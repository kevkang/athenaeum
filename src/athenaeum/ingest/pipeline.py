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
from athenaeum.ingest.grobid import parse_pdf
from athenaeum.storage.models import Paper, PaperSection, PaperSentence

log = structlog.get_logger()

_seg = pysbd.Segmenter(language="en", clean=False, char_span=True)


async def run_ingest(
    paper_id: uuid.UUID,
    oa_pdf_url: str | None,
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    settings: Settings,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        paper = await session.get(Paper, paper_id)
        if paper is None:
            log.warning("ingest.paper_not_found", paper_id=str(paper_id))
            return

        try:
            await _process(paper, oa_pdf_url, session, http, settings)
        except Exception:
            log.exception("ingest.failed", paper_id=str(paper_id))
            paper.status = "failed"
            await session.commit()
            raise


async def _process(
    paper: Paper,
    oa_pdf_url: str | None,
    session: AsyncSession,
    http: httpx.AsyncClient,
    settings: Settings,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)

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

        if not sections:
            log.warning("ingest.no_sections", paper_id=str(paper.id))
            paper.status = "failed"
            await session.commit()
            return

    # Build sections + sentences with char offsets
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
