"""Graph endpoints: brick DAG, ghost papers, co-citation neighbours."""
from __future__ import annotations

import uuid

import neo4j
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import (
    BrickGraphOut,
    BrickNodeOut,
    CoCitationNeighborOut,
    GhostPaperOut,
    GraphBuildsOnOut,
    GraphRestsOnOut,
)
from athenaeum.graph import queries
from athenaeum.storage.models import (
    Brick,
    BrickBuildsOn,
    BrickRestsOn,
    ProjectMember,
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


# ── Brick DAG (served from Postgres for immediate consistency) ─────────────────

@router.get("/projects/{project_id}/graph", response_model=BrickGraphOut)
async def get_project_graph(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> BrickGraphOut:
    await _assert_member(session, project_id, user.id)

    bricks = list(await session.scalars(
        select(Brick)
        .where(Brick.project_id == project_id)
        .order_by(Brick.drafted_at)
    ))
    brick_ids = [b.id for b in bricks]

    builds_on: list[BrickBuildsOn] = []
    rests_on: list[BrickRestsOn] = []
    if brick_ids:
        builds_on = list(await session.scalars(
            select(BrickBuildsOn).where(BrickBuildsOn.brick_id.in_(brick_ids))
        ))
        rests_on = list(await session.scalars(
            select(BrickRestsOn).where(BrickRestsOn.brick_id.in_(brick_ids))
        ))

    return BrickGraphOut(
        nodes=[BrickNodeOut.model_validate(b) for b in bricks],
        builds_on=[GraphBuildsOnOut.model_validate(e) for e in builds_on],
        rests_on=[GraphRestsOnOut.model_validate(e) for e in rests_on],
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
