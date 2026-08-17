"""Unauthenticated public endpoints: project discovery and verification rate stats."""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from athenaeum.api.compose import get_or_create
from athenaeum.api.deps import SessionDep
from athenaeum.archive.athmd import build_athmd, build_dataset_url_resolver
from athenaeum.archive.athmd_render import (
    load_principles_athmd,
    render_athmd_body_html,
    render_athmd_pdf,
    render_athmd_standalone_html,
)
from athenaeum.domain.models import (
    PublicPrinciplesOut,
    PublicProjectOut,
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
    avg_vr = await session.scalar(
        select(func.avg(Synthesis.verification_rate))
        .where(
            Synthesis.project_id == project_id,
            Synthesis.status == "done",
            Synthesis.verification_rate.is_not(None),
        )
    )
    return {
        "statement_count": statement_total or 0,
        "locked_count": statement_locked or 0,
        "synthesis_count": synth_count or 0,
        "avg_verification_rate": float(avg_vr) if avg_vr is not None else None,
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


# ── Verification rate ──────────────────────────────────────────────────────────

@router.get("/verification-rate", response_model=VerificationRateStats)
async def get_verification_rate(
    session: SessionDep,
    project_id: uuid.UUID | None = Query(default=None),
) -> VerificationRateStats:
    if project_id is not None:
        project = await session.get(Project, project_id)
        if project is None or not project.is_public:
            raise HTTPException(status_code=404, detail="Project not found")
        stmt_base = select(Synthesis).where(
            Synthesis.project_id == project_id,
            Synthesis.status == "done",
        )
    else:
        stmt_base = (
            select(Synthesis)
            .join(Project, Synthesis.project_id == Project.id)
            .where(Project.is_public == True, Synthesis.status == "done")  # noqa: E712
        )

    count = await session.scalar(
        select(func.count()).select_from(stmt_base.subquery())
    )
    avg_vr = await session.scalar(
        select(func.avg(Synthesis.verification_rate)).select_from(
            stmt_base.where(Synthesis.verification_rate.is_not(None)).subquery()
        )
    )

    return VerificationRateStats(
        project_id=project_id,
        synthesis_count=count or 0,
        avg_verification_rate=float(avg_vr) if avg_vr is not None else None,
    )


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
