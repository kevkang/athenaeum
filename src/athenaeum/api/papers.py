"""POST /papers  — ingest a paper by DOI into a project.
   GET  /papers/{paper_id} — poll ingestion status.
   GET  /papers/{paper_id}/sections — list sections.
   GET  /papers/{paper_id}/sentences — list sentences (optionally by section).
"""
from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import delete, select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import (
    OpenAlexSearchResult,
    PaperIngest,
    PaperOut,
    PaperUpdate,
    SectionOut,
    SentenceEvidenceOut,
    SentenceOut,
)
from athenaeum.ingest.openalex import fetch_metadata
from athenaeum.storage.models import (
    Paper,
    PaperSection,
    PaperSentence,
    Passage,
    Project,
    ProjectMember,
    ProjectPaper,
    ProvenanceLog,
    Statement,
    StatementEvidence,
    ZoteroCredentials,
)

log = structlog.get_logger()
router = APIRouter(prefix="/papers", tags=["papers"])


def _normalize_doi(doi: str) -> str:
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            return doi[len(prefix):]
    return doi


@router.post("", response_model=PaperOut, status_code=status.HTTP_201_CREATED)
async def ingest_paper(
    payload: PaperIngest,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Paper:
    bare_doi = _normalize_doi(payload.doi.strip())

    # Verify the user is a member of the project
    project = await session.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == payload.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    # Check if we already have this paper
    existing = await session.scalar(select(Paper).where(Paper.doi == bare_doi))

    if existing is not None:
        # Make sure it's linked to this project
        already_linked = await session.scalar(
            select(ProjectPaper).where(
                ProjectPaper.project_id == payload.project_id,
                ProjectPaper.paper_id == existing.id,
            )
        )
        if already_linked is None:
            session.add(
                ProjectPaper(
                    project_id=payload.project_id,
                    paper_id=existing.id,
                    added_by=user.id,
                )
            )
        if payload.found_quote:
            existing.found_quote = payload.found_quote
            existing.found_relevance_note = payload.found_relevance_note
            existing.found_corpus = "openalex"
        await session.commit()
        return existing

    # Fetch metadata from OpenAlex (fast — ~100 ms)
    loop = asyncio.get_running_loop()
    try:
        meta = await loop.run_in_executor(None, fetch_metadata, bare_doi)
    except Exception as exc:
        log.warning("papers.openalex_failed", doi=bare_doi, error=str(exc))
        meta = None

    paper = Paper(
        id=uuid.uuid4(),
        doi=bare_doi,
        openalex_id=meta.openalex_id if meta else None,
        title=meta.title if meta else f"[Pending] {bare_doi}",
        authors_json=meta.authors if meta else None,
        venue=meta.venue if meta else None,
        year=meta.year if meta else None,
        abstract=meta.abstract if meta else None,
        status="ghost",
        found_quote=payload.found_quote,
        found_relevance_note=payload.found_relevance_note,
        found_corpus="openalex" if payload.found_quote else None,
    )
    session.add(paper)

    session.add(
        ProjectPaper(
            project_id=payload.project_id,
            paper_id=paper.id,
            added_by=user.id,
        )
    )
    await session.commit()
    await session.refresh(paper)

    # Enqueue background ingestion
    oa_pdf_url: str | None = meta.oa_pdf_url if meta else None
    try:
        arq_pool = request.app.state.arq_pool
        await arq_pool.enqueue_job("ingest_paper", str(paper.id), oa_pdf_url=oa_pdf_url)
        log.info("papers.enqueued", paper_id=str(paper.id))
    except Exception as exc:
        log.warning("papers.enqueue_failed", paper_id=str(paper.id), error=str(exc))

    return paper


_UPLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_ALLOWED_SUFFIXES = {".pdf", ".txt"}


@router.post("/upload", response_model=PaperOut, status_code=status.HTTP_201_CREATED)
async def upload_paper(
    project_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    doi: str | None = Form(default=None),
    session: SessionDep = ...,
    user: CurrentUserDep = ...,
    request: Request = ...,
) -> Paper:
    settings = get_settings()
    if not settings.s3_bucket:
        raise HTTPException(status_code=501, detail="S3 upload not configured (set S3_BUCKET)")

    suffix = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only PDF and TXT files are supported")

    content = await file.read()
    if len(content) > _UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 50 MB)")

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    paper_id = uuid.uuid4()
    s3_key = f"uploads/{paper_id}{suffix}"

    from athenaeum.storage.s3 import upload_fileobj
    await upload_fileobj(
        content, settings.s3_bucket, s3_key, settings.aws_region, settings.s3_endpoint_url
    )

    bare_doi = _normalize_doi(doi.strip()) if doi else None
    paper = Paper(
        id=paper_id,
        doi=bare_doi,
        title=title.strip() if title else (file.filename or "[Uploaded file]"),
        status="ghost",
    )
    session.add(paper)
    session.add(ProjectPaper(project_id=project_id, paper_id=paper_id, added_by=user.id))
    await session.commit()
    await session.refresh(paper)

    try:
        await request.app.state.arq_pool.enqueue_job(
            "ingest_paper", str(paper_id), s3_key=s3_key
        )
        log.info("papers.upload_enqueued", paper_id=str(paper_id), key=s3_key)
    except Exception as exc:
        log.warning("papers.upload_enqueue_failed", paper_id=str(paper_id), error=str(exc))

    return paper


@router.patch("/{paper_id}", response_model=PaperOut)
async def update_paper(
    paper_id: uuid.UUID,
    payload: PaperUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Paper:
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if payload.title is not None:
        paper.title = payload.title
    if payload.abstract is not None:
        paper.abstract = payload.abstract
    await session.commit()
    await session.refresh(paper)
    return paper


async def _enqueue_graph_sync(request: Request, project_id: uuid.UUID) -> None:
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is not None:
        try:
            await pool.enqueue_job(
                "sync_project_graph",
                str(project_id),
                _job_id=f"sync_graph_{project_id}",
            )
        except Exception:
            log.warning("graph_sync.enqueue_failed", project_id=str(project_id))


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(
    paper_id: uuid.UUID,
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    """Unlinks a paper from the given project. If no other project still
    references the paper, the underlying Paper row (and its sections,
    sentences, and passages) is fully deleted too."""
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    link = await session.get(ProjectPaper, (project_id, paper_id))
    if link is None:
        raise HTTPException(status_code=404, detail="Paper is not part of this project")

    await session.delete(link)
    await session.flush()

    other_link = await session.scalar(
        select(ProjectPaper).where(ProjectPaper.paper_id == paper_id)
    )
    if other_link is None:
        # Last project referencing this paper — fully delete it, and clean up
        # statement-evidence rows that pointed at it or its passages (not
        # FK-constrained, since target_id is polymorphic across target_kind).
        passage_ids = list(await session.scalars(
            select(Passage.id).where(Passage.paper_id == paper_id)
        ))
        await session.execute(
            delete(StatementEvidence).where(
                (StatementEvidence.target_kind == "paper")
                & (StatementEvidence.target_id == paper_id)
            )
        )
        if passage_ids:
            await session.execute(
                delete(StatementEvidence).where(
                    (StatementEvidence.target_kind == "passage")
                    & (StatementEvidence.target_id.in_(passage_ids))
                )
            )
        paper = await session.get(Paper, paper_id)
        if paper is not None:
            await session.delete(paper)

    session.add(
        ProvenanceLog(
            id=uuid.uuid4(),
            project_id=project_id,
            kind="paper_removed",
            payload_json={"paper_id": str(paper_id), "fully_deleted": other_link is None},
        )
    )
    await session.commit()
    await _enqueue_graph_sync(request, project_id)


@router.post("/{paper_id}/reupload", response_model=PaperOut)
async def reupload_paper(
    paper_id: uuid.UUID,
    file: UploadFile = File(...),
    session: SessionDep = ...,
    user: CurrentUserDep = ...,
    request: Request = ...,
) -> Paper:
    settings = get_settings()
    if not settings.s3_bucket:
        raise HTTPException(status_code=501, detail="S3 upload not configured (set S3_BUCKET)")

    suffix = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only PDF and TXT files are supported")

    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    content = await file.read()
    if len(content) > _UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 50 MB)")

    s3_key = f"uploads/{paper_id}{suffix}"
    from athenaeum.storage.s3 import upload_fileobj
    await upload_fileobj(
        content, settings.s3_bucket, s3_key, settings.aws_region, settings.s3_endpoint_url
    )

    paper.status = "ghost"
    await session.commit()
    await session.refresh(paper)

    try:
        await request.app.state.arq_pool.enqueue_job("ingest_paper", str(paper_id), s3_key=s3_key)
        log.info("papers.reupload_enqueued", paper_id=str(paper_id), key=s3_key)
    except Exception as exc:
        log.warning("papers.reupload_enqueue_failed", paper_id=str(paper_id), error=str(exc))

    return paper


@router.post("/{paper_id}/summarize", response_model=PaperOut)
async def summarize_paper(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> Paper:
    settings = get_settings()

    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if paper.status != "full_text":
        raise HTTPException(status_code=400, detail="Paper must have full text before summarizing")

    rows = await session.scalars(
        select(PaperSentence)
        .where(PaperSentence.paper_id == paper_id)
        .order_by(PaperSentence.ord)
    )
    text = " ".join(s.text for s in rows)
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text content found for this paper")

    from athenaeum.provider.base import Message
    from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user

    try:
        provider = await get_provider_for_user(session, user.id, settings.summarize_model, settings)
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc
    message, _ = await provider.complete(
        messages=[
            Message(
                role="user",
                content=(
                    f'Paper title: "{paper.title}"\n\n'
                    f"Full text (excerpt):\n{text[:8000]}\n\n"
                    "Write a concise abstract (150–250 words) covering the paper's "
                    "objectives, methods, key findings, and conclusions. "
                    "Output only the abstract text."
                ),
            )
        ],
        system="You are a scientific writing assistant. Write accurate, well-structured abstracts.",
        max_tokens=512,
    )

    if not message:
        raise HTTPException(status_code=502, detail="LLM returned no content")

    paper.abstract = message.strip()
    await session.commit()
    await session.refresh(paper)
    return paper


@router.post("/{paper_id}/retry", response_model=PaperOut)
async def retry_ingest(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Paper:
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    if paper.zotero_item_key and paper.zotero_source == "local":
        paper.status = "ghost"
        await session.commit()
        await session.refresh(paper)

        try:
            await request.app.state.arq_pool.enqueue_job(
                "ingest_local_zotero_item", str(paper.id), paper.zotero_item_key
            )
            log.info("papers.local_zotero_retry_enqueued", paper_id=str(paper.id))
        except Exception as exc:
            log.warning(
                "papers.local_zotero_retry_enqueue_failed", paper_id=str(paper.id), error=str(exc)
            )

        return paper

    if paper.zotero_item_key:
        if paper.zotero_imported_by is None or (
            await session.get(ZoteroCredentials, paper.zotero_imported_by)
        ) is None:
            raise HTTPException(
                status_code=409,
                detail="The Zotero account that imported this paper is no longer connected",
            )

        paper.status = "ghost"
        await session.commit()
        await session.refresh(paper)

        try:
            await request.app.state.arq_pool.enqueue_job(
                "ingest_zotero_item",
                str(paper.id),
                paper.zotero_item_key,
                str(paper.zotero_imported_by),
            )
            log.info("papers.zotero_retry_enqueued", paper_id=str(paper.id))
        except Exception as exc:
            log.warning("papers.zotero_retry_enqueue_failed", paper_id=str(paper.id), error=str(exc))

        return paper

    paper.status = "ghost"
    await session.commit()
    await session.refresh(paper)

    try:
        await request.app.state.arq_pool.enqueue_job("ingest_paper", str(paper.id), oa_pdf_url=None)
        log.info("papers.retry_enqueued", paper_id=str(paper.id))
    except Exception as exc:
        log.warning("papers.retry_enqueue_failed", paper_id=str(paper.id), error=str(exc))

    return paper


@router.get("/search", response_model=list[OpenAlexSearchResult])
async def search_openalex(
    q: str = Query(min_length=3, max_length=500),
    per_page: int = Query(default=15, ge=1, le=25),
    user: CurrentUserDep = ...,
) -> list[OpenAlexSearchResult]:
    from athenaeum.ingest.openalex import search_works

    loop = asyncio.get_running_loop()
    try:
        hits = await loop.run_in_executor(None, search_works, q, per_page)
    except Exception as exc:
        log.warning("papers.openalex_search_failed", q=q, error=str(exc))
        raise HTTPException(status_code=502, detail="OpenAlex search failed")
    return [
        OpenAlexSearchResult(
            openalex_id=h.openalex_id,
            doi=h.doi,
            title=h.title,
            first_author=h.first_author,
            venue=h.venue,
            year=h.year,
            abstract=h.abstract,
            cited_by_count=h.cited_by_count,
        )
        for h in hits
    ]


@router.get("/{paper_id}", response_model=PaperOut)
async def get_paper(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> Paper:
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@router.get("/{paper_id}/sections", response_model=list[SectionOut])
async def list_sections(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[PaperSection]:
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    rows = await session.scalars(
        select(PaperSection)
        .where(PaperSection.paper_id == paper_id)
        .order_by(PaperSection.ord)
    )
    return list(rows)


@router.get("/{paper_id}/sentences", response_model=list[SentenceOut])
async def list_sentences(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    section_id: uuid.UUID | None = Query(default=None),
) -> list[PaperSentence]:
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    stmt = (
        select(PaperSentence)
        .where(PaperSentence.paper_id == paper_id)
        .order_by(PaperSentence.ord)
    )
    if section_id is not None:
        stmt = stmt.where(PaperSentence.section_id == section_id)

    rows = await session.scalars(stmt)
    return list(rows)


@router.get("/{paper_id}/evidence-sentences", response_model=list[SentenceEvidenceOut])
async def list_evidence_sentences(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[SentenceEvidenceOut]:
    """Which of this paper's sentences are evidence for which statement(s) —
    powers the Read view's statement-evidence highlight. `target_kind='passage'`
    evidence can point at either a real Passage.id (expand to all its
    sentence_ids) or directly at a PaperSentence.id (the reader's
    drag-to-workspace flow) — both are resolved here, in 3 queries total."""
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")

    sentence_ids = set(
        await session.scalars(select(PaperSentence.id).where(PaperSentence.paper_id == paper_id))
    )
    passage_rows = (
        await session.execute(
            select(Passage.id, Passage.sentence_ids).where(Passage.paper_id == paper_id)
        )
    ).all()
    passage_sentence_map = {row.id: row.sentence_ids for row in passage_rows}

    combined_ids = sentence_ids | set(passage_sentence_map)
    if not combined_ids:
        return []

    rows = (
        await session.execute(
            select(
                StatementEvidence.target_id,
                StatementEvidence.statement_id,
                StatementEvidence.role,
                Statement.assertion,
            )
            .join(Statement, Statement.id == StatementEvidence.statement_id)
            .where(
                StatementEvidence.target_kind == "passage",
                StatementEvidence.target_id.in_(combined_ids),
            )
        )
    ).all()

    result: list[SentenceEvidenceOut] = []
    for target_id, statement_id, role, assertion in rows:
        sids = passage_sentence_map.get(target_id, [target_id])
        result.extend(
            SentenceEvidenceOut(sentence_id=sid, statement_id=statement_id, assertion=assertion, role=role)
            for sid in sids
        )
    return result
