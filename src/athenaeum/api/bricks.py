"""Brick CRUD, cement transition, rests_on and builds_on edge management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import (
    BrickCreate,
    BrickOut,
    BrickUpdate,
    BuildsOnCreate,
    BuildsOnOut,
    RestsOnCreate,
    RestsOnOut,
)
from athenaeum.storage.models import (
    Brick,
    BrickBuildsOn,
    BrickRestsOn,
    Project,
    ProjectMember,
    ProvenanceLog,
)

log = structlog.get_logger()
router = APIRouter(prefix="/bricks", tags=["bricks"])


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


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _assert_member(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")


async def _get_brick_or_404(session: AsyncSession, brick_id: uuid.UUID) -> Brick:
    brick = await session.get(Brick, brick_id)
    if brick is None:
        raise HTTPException(status_code=404, detail="Brick not found")
    return brick


async def _would_create_cycle(
    session: AsyncSession,
    brick_id: uuid.UUID,
    parent_brick_id: uuid.UUID,
) -> bool:
    """Return True if adding brick_id→parent_brick_id creates a DAG cycle."""
    if brick_id == parent_brick_id:
        return True
    result = await session.execute(
        text("""
            WITH RECURSIVE ancestors AS (
                SELECT parent_brick_id AS id
                FROM brick_builds_on
                WHERE brick_id = :parent_id
                UNION ALL
                SELECT bbo.parent_brick_id
                FROM brick_builds_on bbo
                JOIN ancestors a ON bbo.brick_id = a.id
            )
            SELECT 1 FROM ancestors WHERE id = :brick_id LIMIT 1
        """),
        {"parent_id": str(parent_brick_id), "brick_id": str(brick_id)},
    )
    return result.fetchone() is not None


def _log_provenance(
    session: AsyncSession,
    project_id: uuid.UUID,
    kind: str,
    payload: dict,
) -> None:
    session.add(
        ProvenanceLog(
            id=uuid.uuid4(),
            project_id=project_id,
            kind=kind,
            payload_json=payload,
        )
    )


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.post("", response_model=BrickOut, status_code=201)
async def create_brick(
    payload: BrickCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Brick:
    await _assert_member(session, payload.project_id, user.id)

    brick = Brick(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        type=payload.type,
        assertion=payload.assertion,
        built_from_synthesis_id=payload.built_from_synthesis_id,
        status="draft",
    )
    session.add(brick)
    _log_provenance(
        session,
        payload.project_id,
        "brick_created",
        {"brick_id": str(brick.id), "type": payload.type},
    )
    await session.commit()
    await session.refresh(brick)
    await _enqueue_graph_sync(request, payload.project_id)
    return brick


@router.get("/{brick_id}", response_model=BrickOut)
async def get_brick(
    brick_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> Brick:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)
    return brick


@router.patch("/{brick_id}", response_model=BrickOut)
async def update_brick(
    brick_id: uuid.UUID,
    payload: BrickUpdate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Brick:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)

    was_cemented = brick.status == "cemented"
    changed: dict = {}

    if payload.assertion is not None and payload.assertion != brick.assertion:
        brick.assertion = payload.assertion
        changed["assertion"] = True
    if payload.type is not None and payload.type != brick.type:
        brick.type = payload.type
        changed["type"] = payload.type

    if changed and was_cemented:
        brick.status = "draft"
        brick.cemented_at = None
        _log_provenance(
            session,
            brick.project_id,
            "brick_uncemented",
            {"brick_id": str(brick.id), "reason": "edited", "changed": list(changed)},
        )

    await session.commit()
    await session.refresh(brick)
    await _enqueue_graph_sync(request, brick.project_id)
    return brick


@router.delete("/{brick_id}", status_code=204)
async def delete_brick(
    brick_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)
    if brick.status == "cemented":
        raise HTTPException(status_code=409, detail="Cannot delete a cemented brick")
    project_id = brick.project_id
    _log_provenance(
        session,
        brick.project_id,
        "brick_deleted",
        {"brick_id": str(brick.id), "assertion_preview": brick.assertion[:100]},
    )
    await session.delete(brick)
    await session.commit()
    await _enqueue_graph_sync(request, project_id)


# ── State transition ───────────────────────────────────────────────────────────

@router.post("/{brick_id}/cement", response_model=BrickOut)
async def cement_brick(
    brick_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Brick:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)

    if brick.status == "cemented":
        raise HTTPException(status_code=409, detail="Brick is already cemented")

    brick.status = "cemented"
    brick.cemented_at = datetime.now(timezone.utc)
    _log_provenance(
        session,
        brick.project_id,
        "brick_cemented",
        {"brick_id": str(brick.id), "assertion_preview": brick.assertion[:200]},
    )
    await session.commit()
    await session.refresh(brick)
    await _enqueue_graph_sync(request, brick.project_id)
    return brick


# ── rests_on edges ─────────────────────────────────────────────────────────────

@router.post("/{brick_id}/rests_on", response_model=RestsOnOut, status_code=201)
async def add_rests_on(
    brick_id: uuid.UUID,
    payload: RestsOnCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> BrickRestsOn:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)

    existing = await session.get(
        BrickRestsOn, (brick_id, payload.target_kind, payload.target_id)
    )
    if existing is not None:
        return existing

    edge = BrickRestsOn(
        brick_id=brick_id,
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        role=payload.role,
    )
    session.add(edge)
    await session.commit()
    await _enqueue_graph_sync(request, brick.project_id)
    return edge


@router.get("/{brick_id}/rests_on", response_model=list[RestsOnOut])
async def list_rests_on(
    brick_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[BrickRestsOn]:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)
    rows = await session.scalars(
        select(BrickRestsOn).where(BrickRestsOn.brick_id == brick_id)
    )
    return list(rows)


@router.delete("/{brick_id}/rests_on/{target_kind}/{target_id}", status_code=204)
async def remove_rests_on(
    brick_id: uuid.UUID,
    target_kind: str,
    target_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)
    edge = await session.get(BrickRestsOn, (brick_id, target_kind, target_id))
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found")
    await session.delete(edge)
    await session.commit()
    await _enqueue_graph_sync(request, brick.project_id)


# ── builds_on edges ────────────────────────────────────────────────────────────

@router.post("/{brick_id}/builds_on", response_model=BuildsOnOut, status_code=201)
async def add_builds_on(
    brick_id: uuid.UUID,
    payload: BuildsOnCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> BrickBuildsOn:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)

    parent = await _get_brick_or_404(session, payload.parent_brick_id)
    if parent.project_id != brick.project_id:
        raise HTTPException(status_code=400, detail="Parent brick is in a different project")

    if await _would_create_cycle(session, brick_id, payload.parent_brick_id):
        raise HTTPException(status_code=409, detail="Adding this edge would create a cycle")

    existing = await session.get(BrickBuildsOn, (brick_id, payload.parent_brick_id))
    if existing is not None:
        return existing

    edge = BrickBuildsOn(
        brick_id=brick_id,
        parent_brick_id=payload.parent_brick_id,
        ord=payload.ord,
    )
    session.add(edge)
    await session.commit()
    await _enqueue_graph_sync(request, brick.project_id)
    return edge


@router.get("/{brick_id}/builds_on", response_model=list[BuildsOnOut])
async def list_builds_on(
    brick_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[BrickBuildsOn]:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)
    rows = await session.scalars(
        select(BrickBuildsOn)
        .where(BrickBuildsOn.brick_id == brick_id)
        .order_by(BrickBuildsOn.ord)
    )
    return list(rows)


@router.delete("/{brick_id}/builds_on/{parent_id}", status_code=204)
async def remove_builds_on(
    brick_id: uuid.UUID,
    parent_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    brick = await _get_brick_or_404(session, brick_id)
    await _assert_member(session, brick.project_id, user.id)
    edge = await session.get(BrickBuildsOn, (brick_id, parent_id))
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found")
    await session.delete(edge)
    await session.commit()
    await _enqueue_graph_sync(request, brick.project_id)


# ── Project-scoped list ────────────────────────────────────────────────────────

@router.get("/by-project/{project_id}", response_model=list[BrickOut])
async def list_bricks(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[Brick]:
    await _assert_member(session, project_id, user.id)
    rows = await session.scalars(
        select(Brick)
        .where(Brick.project_id == project_id)
        .order_by(Brick.drafted_at.desc())
    )
    return list(rows)
