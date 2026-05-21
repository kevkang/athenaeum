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
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import PaperIngest, PaperOut, SectionOut, SentenceOut
from athenaeum.ingest.openalex import fetch_metadata
from athenaeum.storage.models import Paper, PaperSection, PaperSentence, Project, ProjectMember, ProjectPaper

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
    await upload_fileobj(content, settings.s3_bucket, s3_key, settings.aws_region)

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

    paper.status = "ghost"
    await session.commit()
    await session.refresh(paper)

    try:
        await request.app.state.arq_pool.enqueue_job("ingest_paper", str(paper.id), oa_pdf_url=None)
        log.info("papers.retry_enqueued", paper_id=str(paper.id))
    except Exception as exc:
        log.warning("papers.retry_enqueue_failed", paper_id=str(paper.id), error=str(exc))

    return paper


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
