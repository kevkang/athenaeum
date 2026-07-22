"""Graph endpoints: statement relation graph, ghost papers, co-citation neighbours."""
from __future__ import annotations

import uuid

import neo4j
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import (
    CoCitationNeighborOut,
    GhostPaperOut,
    StatementEdgeOut,
    StatementGraphOut,
    StatementNodeOut,
)
from athenaeum.graph import queries
from athenaeum.storage.models import (
    ProjectMember,
    Statement,
    StatementEdge,
    StatementEvidence,
)

log = structlog.get_logger()
router = APIRouter(tags=["graph"])


def _require_neo4j(request: Request) -> neo4j.AsyncDriver:
    driver = getattr(request.app.state, "neo4j_driver", None)
    if driver is None:
        raise HTTPException(status_code=503, detail="Graph database unavailable")
    return driver


async def _assert_member(session, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")


# ── Statement graph (served from Postgres for immediate consistency) ───────────

@router.get("/projects/{project_id}/graph", response_model=StatementGraphOut)
async def get_project_graph(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> StatementGraphOut:
    await _assert_member(session, project_id, user.id)

    statements = list(await session.scalars(
        select(Statement)
        .where(Statement.project_id == project_id)
        .order_by(Statement.drafted_at)
    ))
    statement_ids = [s.id for s in statements]

    edges: list[StatementEdge] = []
    evidence_counts: dict[uuid.UUID, int] = {}
    if statement_ids:
        edges = list(await session.scalars(
            select(StatementEdge).where(
                or_(
                    StatementEdge.from_statement_id.in_(statement_ids),
                    StatementEdge.to_statement_id.in_(statement_ids),
                )
            )
        ))
        count_rows = await session.execute(
            select(StatementEvidence.statement_id, func.count())
            .where(StatementEvidence.statement_id.in_(statement_ids))
            .group_by(StatementEvidence.statement_id)
        )
        evidence_counts = {sid: count for sid, count in count_rows.all()}

    return StatementGraphOut(
        nodes=[
            StatementNodeOut(
                id=s.id,
                project_id=s.project_id,
                type=s.type,
                status=s.status,
                assertion=s.assertion,
                evidence_count=evidence_counts.get(s.id, 0),
            )
            for s in statements
        ],
        edges=[StatementEdgeOut.model_validate(e) for e in edges],
    )


# ── Ghost papers (Neo4j) ───────────────────────────────────────────────────────

@router.get("/projects/{project_id}/ghost_papers", response_model=list[GhostPaperOut])
async def get_ghost_papers(
    project_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[GhostPaperOut]:
    await _assert_member(session, project_id, user.id)
    driver = _require_neo4j(request)
    rows = await queries.ghost_papers(driver, str(project_id))
    return [GhostPaperOut(**r) for r in rows]


# ── Co-citation neighbours (Neo4j) ────────────────────────────────────────────

@router.get("/papers/{paper_id}/co_citation_neighbors", response_model=list[CoCitationNeighborOut])
async def get_co_citation_neighbors(
    paper_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(..., description="Used for access control"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[CoCitationNeighborOut]:
    await _assert_member(session, project_id, user.id)
    driver = _require_neo4j(request)
    rows = await queries.co_citation_neighbors(driver, str(paper_id), limit)
    return [CoCitationNeighborOut(**r) for r in rows]
