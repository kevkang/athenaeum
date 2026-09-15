"""Unauthenticated public endpoints: project discovery and the published
measurement stats.

Each number answers a different question, and none implies another (see
README's "What is and isn't measured"):

- **Synthesis relevance** (`/synthesis-relevance-rate`) — the share of
  Synthesis passages an independent model judged relevant to the claim. A
  model's semantic judgment, not a string check.
- **Backtrace resolution** (`/backtrace-resolution`) — of the citation hops
  users chased, the share that reached a verbatim quote, broken out by depth.

Verbatim accuracy is not published as a rate; the README describes how it is
established. Whether cited evidence *supports* a claim is Audit's question,
which has no published rate yet.

Synthesis rates count only syntheses run on this server. Imported ones carry
numbers measured elsewhere, which this server cannot vouch for.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import ColumnElement, func, select

from athenaeum.api.compose import get_or_create
from athenaeum.api.deps import SessionDep
from athenaeum.archive.athmd import build_athmd, build_dataset_url_resolver
from athenaeum.archive.athmd_render import (
    load_principles_athmd,
    render_athmd_body_html,
    render_athmd_pdf,
    render_athmd_standalone_html,
)
from athenaeum.domain import backtrace_stats as bt_stats
from athenaeum.domain.models import (
    BacktraceResolutionOut,
    PublicPrinciplesOut,
    PublicProjectOut,
    RelevanceRateStats,
    StatementOut,
    VerificationRateStats,
)
from athenaeum.storage.models import Project, Statement, Synthesis

log = structlog.get_logger()
router = APIRouter(prefix="/public", tags=["public"])


async def _require_public_project(session, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or not project.is_public:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _project_stats(session, project_id: uuid.UUID) -> dict[str, Any]:
    statement_total = await session.scalar(
        select(func.count()).select_from(Statement).where(Statement.project_id == project_id)
    )
    statement_locked = await session.scalar(
        select(func.count())
        .select_from(Statement)
        .where(Statement.project_id == project_id, Statement.status == "locked")
    )
    synth_count = await session.scalar(
        select(func.count())
        .select_from(Synthesis)
        .where(Synthesis.project_id == project_id, Synthesis.status == "done")
    )
    # Rates average only syntheses run on this server; imported ones carry
    # numbers measured elsewhere.
    local_done = (
        Synthesis.project_id == project_id,
        Synthesis.status == "done",
        Synthesis.imported_at.is_(None),
    )
    avg_vr = await session.scalar(
        select(func.avg(Synthesis.verification_rate))
        .where(*local_done, Synthesis.verification_rate.is_not(None))
    )
    avg_rr = await session.scalar(
        select(func.avg(Synthesis.relevance_rate))
        .where(*local_done, Synthesis.relevance_rate.is_not(None))
    )
    return {
        "statement_count": statement_total or 0,
        "locked_count": statement_locked or 0,
        "synthesis_count": synth_count or 0,
        "avg_verification_rate": float(avg_vr) if avg_vr is not None else None,
        "avg_relevance_rate": float(avg_rr) if avg_rr is not None else None,
    }


# ── Public project discovery ───────────────────────────────────────────────────

@router.get("/projects", response_model=list[PublicProjectOut])
async def list_public_projects(
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[PublicProjectOut]:
    projects = list(await session.scalars(
        select(Project)
        .where(Project.is_public == True)  # noqa: E712
        .order_by(Project.created_at.desc())
        .limit(limit)
        .offset(offset)
    ))

    result = []
    for p in projects:
        stats = await _project_stats(session, p.id)
        result.append(PublicProjectOut(
            id=p.id,
            title=p.title,
            description=p.description,
            license=p.license,
            created_at=p.created_at,
            **stats,
        ))
    return result


@router.get("/projects/{project_id}", response_model=PublicProjectOut)
async def get_public_project(
    project_id: uuid.UUID,
    session: SessionDep,
) -> PublicProjectOut:
    project = await _require_public_project(session, project_id)
    stats = await _project_stats(session, project_id)
    return PublicProjectOut(
        id=project.id,
        title=project.title,
        description=project.description,
        license=project.license,
        created_at=project.created_at,
        **stats,
    )


@router.get("/projects/{project_id}/statements", response_model=list[StatementOut])
async def list_public_project_statements(
    project_id: uuid.UUID,
    session: SessionDep,
) -> list[Statement]:
    await _require_public_project(session, project_id)
    rows = await session.scalars(
        select(Statement)
        .where(Statement.project_id == project_id, Statement.status == "locked")
        .order_by(Statement.locked_at.desc())
    )
    return list(rows)


# ── Synthesis relevance rate ───────────────────────────────────────────────────


async def _public_synthesis_filters(
    session: SessionDep, project_id: uuid.UUID | None
) -> list[ColumnElement[bool]]:
    """Which syntheses a public rate may count: completed, run on this server
    rather than imported, and in a public project (or in the one public
    project requested). Filters are applied straight to the aggregate query,
    so the average reads exactly these rows and nothing else."""
    filters: list[ColumnElement[bool]] = [
        Synthesis.status == "done",
        Synthesis.imported_at.is_(None),
    ]
    if project_id is not None:
        await _require_public_project(session, project_id)
        filters.append(Synthesis.project_id == project_id)
    else:
        filters.append(
            Synthesis.project_id.in_(select(Project.id).where(Project.is_public.is_(True)))
        )
    return filters


@router.get("/synthesis-relevance-rate", response_model=RelevanceRateStats)
async def get_synthesis_relevance_rate(
    session: SessionDep,
    project_id: uuid.UUID | None = Query(default=None),
) -> RelevanceRateStats:
    """Average share of Synthesis passages an independent model judged
    relevant to the claim, over syntheses that measured it.

    This is a model's semantic judgment. It is not a check that quotes are
    verbatim, and not a judgment that evidence supports the claim, which is
    Audit's question. `synthesis_count` is how many syntheses the average
    covers.
    """
    filters = await _public_synthesis_filters(session, project_id)
    measured = [*filters, Synthesis.relevance_rate.is_not(None)]
    count = await session.scalar(select(func.count()).select_from(Synthesis).where(*measured))
    avg_rr = await session.scalar(select(func.avg(Synthesis.relevance_rate)).where(*measured))
    return RelevanceRateStats(
        project_id=project_id,
        synthesis_count=count or 0,
        avg_relevance_rate=float(avg_rr) if avg_rr is not None else None,
    )


# ── Retired combined rate ──────────────────────────────────────────────────────


@router.get(
    "/verification-rate",
    response_model=VerificationRateStats,
    deprecated=True,
    summary="Deprecated: retired combined check. Use /public/synthesis-relevance-rate",
)
async def get_verification_rate(
    session: SessionDep,
    project_id: uuid.UUID | None = Query(default=None),
) -> VerificationRateStats:
    """The retired combined metric: the share of passages a model judged both
    "plausibly verbatim" and "relevant". New syntheses no longer record it, so
    this reflects only older ones. Kept so the published URL still answers.
    """
    filters = await _public_synthesis_filters(session, project_id)
    count = await session.scalar(select(func.count()).select_from(Synthesis).where(*filters))
    avg_vr = await session.scalar(
        select(func.avg(Synthesis.verification_rate))
        .where(*filters, Synthesis.verification_rate.is_not(None))
    )
    return VerificationRateStats(
        project_id=project_id,
        synthesis_count=count or 0,
        avg_verification_rate=float(avg_vr) if avg_vr is not None else None,
    )


# ── Backtrace resolution coverage ──────────────────────────────────────────────

@router.get("/backtrace-resolution", response_model=BacktraceResolutionOut)
async def get_backtrace_resolution(
    session: SessionDep,
    project_id: uuid.UUID | None = Query(default=None),
) -> BacktraceResolutionOut:
    """What share of chased citation hops reached a verbatim quote, across
    public projects, broken out by how far back from the origin claim they
    went.

    Read the per-depth numbers, not just the headline: resolution falls off
    with depth, so a healthy overall rate can coexist with tips that almost
    never resolve. The denominator counts hops users actually chased, not
    every reference that was available to chase — see
    domain/backtrace_stats.py.
    """
    if project_id is not None:
        await _require_public_project(session, project_id)

    coverage = await bt_stats.load_coverage(
        session, project_id=project_id, public_only=True
    )
    return bt_stats.to_out(coverage, project_id=project_id)


# ── Principles: the .athmd proof-of-concept, shown on the home page ────────────

@router.get("/principles", response_model=PublicPrinciplesOut)
async def get_public_principles() -> PublicPrinciplesOut:
    text = load_principles_athmd()
    return PublicPrinciplesOut(html=render_athmd_body_html(text))


@router.get("/principles.athmd")
async def download_public_principles_athmd() -> Response:
    return Response(
        content=load_principles_athmd(),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="principles.athmd"'},
    )


@router.get("/principles.pdf")
async def download_public_principles_pdf(request: Request) -> Response:
    browser = request.app.state.pw_browser
    if browser is None:
        raise HTTPException(status_code=503, detail="PDF rendering is unavailable")
    text = load_principles_athmd()
    pdf_bytes = await render_athmd_pdf(browser, text, title="Athenaeum Principles")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="athenaeum-principles.pdf"'},
    )


# ── Public project Compose document rendering ───────────────────────────────

@router.get("/projects/{project_id}/compose-document/render.html")
async def render_public_compose_document_html(
    project_id: uuid.UUID, session: SessionDep,
) -> Response:
    project = await _require_public_project(session, project_id)
    doc = await get_or_create(session, project_id)
    text = await build_athmd(session, doc.id)
    resolver = await build_dataset_url_resolver(session, text)
    html_out = render_athmd_standalone_html(
        text, title=project.title, dataset_url_resolver=resolver
    )
    safe_title = project.title[:60].replace("/", "-").replace(" ", "_")
    return Response(
        content=html_out,
        media_type="text/html",
        headers={"Content-Disposition": f'inline; filename="{safe_title}.html"'},
    )


@router.get("/projects/{project_id}/compose-document/render.pdf")
async def render_public_compose_document_pdf(
    project_id: uuid.UUID, request: Request, session: SessionDep,
) -> Response:
    project = await _require_public_project(session, project_id)
    browser = request.app.state.pw_browser
    if browser is None:
        raise HTTPException(status_code=503, detail="PDF rendering is unavailable")
    doc = await get_or_create(session, project_id)
    text = await build_athmd(session, doc.id)
    resolver = await build_dataset_url_resolver(session, text)
    pdf_bytes = await render_athmd_pdf(
        browser, text, title=project.title, dataset_url_resolver=resolver
    )
    safe_title = project.title[:60].replace("/", "-").replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
    )
