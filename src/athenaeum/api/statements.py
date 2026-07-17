"""Statement CRUD, lock/unlock transitions, evidence and edge management."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import (
    EvidenceCreate,
    EvidenceOut,
    StatementCreate,
    StatementEdgeCreate,
    StatementEdgeOut,
    StatementOut,
    StatementUpdate,
)
from athenaeum.storage.models import (
    ProjectMember,
    ProvenanceLog,
    Statement,
    StatementEdge,
    StatementEvidence,
)

log = structlog.get_logger()
router = APIRouter(prefix="/statements", tags=["statements"])


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


async def _get_statement_or_404(session: AsyncSession, statement_id: uuid.UUID) -> Statement:
    statement = await session.get(Statement, statement_id)
    if statement is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    return statement


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

@router.post("", response_model=StatementOut, status_code=201)
async def create_statement(
    payload: StatementCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Statement:
    await _assert_member(session, payload.project_id, user.id)

    statement = Statement(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        author_id=user.id,
        type=payload.type,
        assertion=payload.assertion,
        built_from_synthesis_id=payload.built_from_synthesis_id,
        status="open" if payload.type == "question" else "draft",
    )
    session.add(statement)
    _log_provenance(
        session,
        payload.project_id,
        "statement_created",
        {"statement_id": str(statement.id), "type": payload.type},
    )
    await session.commit()
    await session.refresh(statement)
    await _enqueue_graph_sync(request, payload.project_id)
    return statement


@router.get("/{statement_id}", response_model=StatementOut)
async def get_statement(
    statement_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> Statement:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)
    return statement


@router.patch("/{statement_id}", response_model=StatementOut)
async def update_statement(
    statement_id: uuid.UUID,
    payload: StatementUpdate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Statement:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)

    was_locked = statement.status == "locked"
    changed: dict = {}

    if payload.assertion is not None and payload.assertion != statement.assertion:
        statement.assertion = payload.assertion
        changed["assertion"] = True
    if payload.type is not None and payload.type != statement.type:
        statement.type = payload.type
        changed["type"] = payload.type
    if payload.status is not None and payload.status != statement.status:
        if statement.type != "question":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Use POST /statements/{id}/lock to change status of "
                    "non-question statements"
                ),
            )
        statement.status = payload.status
        changed["status"] = payload.status

    if changed and was_locked:
        statement.status = "draft"
        statement.locked_at = None
        _log_provenance(
            session,
            statement.project_id,
            "statement_unlocked",
            {"statement_id": str(statement.id), "reason": "edited", "changed": list(changed)},
        )

    await session.commit()
    await session.refresh(statement)
    await _enqueue_graph_sync(request, statement.project_id)
    return statement


@router.delete("/{statement_id}", status_code=204)
async def delete_statement(
    statement_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)
    if statement.status == "locked":
        raise HTTPException(status_code=409, detail="Cannot delete a locked statement")
    project_id = statement.project_id
    _log_provenance(
        session,
        statement.project_id,
        "statement_deleted",
        {"statement_id": str(statement.id), "assertion_preview": statement.assertion[:100]},
    )
    # Clean up polymorphic evidence rows from OTHER statements that cited this
    # one as evidence (target_kind='statement', target_id=<this id>). These are
    # not FK-constrained (target_id is polymorphic), so Postgres won't cascade.
    await session.execute(
        delete(StatementEvidence).where(
            (StatementEvidence.target_kind == "statement")
            & (StatementEvidence.target_id == statement_id)
        )
    )
    await session.delete(statement)
    await session.commit()
    await _enqueue_graph_sync(request, project_id)


# ── State transition ───────────────────────────────────────────────────────────

@router.post("/{statement_id}/lock", response_model=StatementOut)
async def lock_statement(
    statement_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Statement:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)

    if statement.status == "locked":
        raise HTTPException(status_code=409, detail="Statement is already locked")

    statement.status = "locked"
    statement.locked_at = datetime.now(timezone.utc)
    _log_provenance(
        session,
        statement.project_id,
        "statement_locked",
        {"statement_id": str(statement.id), "assertion_preview": statement.assertion[:200]},
    )
    await session.commit()
    await session.refresh(statement)
    await _enqueue_graph_sync(request, statement.project_id)
    return statement


@router.post("/{statement_id}/unlock", response_model=StatementOut)
async def unlock_statement(
    statement_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Statement:
    """Locking is not immutable — a statement can always be unlocked again."""
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)

    if statement.status != "locked":
        raise HTTPException(status_code=409, detail="Statement is not locked")

    statement.status = "draft"
    statement.locked_at = None
    _log_provenance(
        session,
        statement.project_id,
        "statement_unlocked",
        {"statement_id": str(statement.id), "reason": "manual"},
    )
    await session.commit()
    await session.refresh(statement)
    await _enqueue_graph_sync(request, statement.project_id)
    return statement


# ── Evidence (nested under a statement) ─────────────────────────────────────────

@router.post("/{statement_id}/evidence", response_model=EvidenceOut, status_code=201)
async def add_evidence(
    statement_id: uuid.UUID,
    payload: EvidenceCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> StatementEvidence:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)

    existing = await session.get(
        StatementEvidence, (statement_id, payload.target_kind, payload.target_id)
    )
    if existing is not None:
        return existing

    edge = StatementEvidence(
        statement_id=statement_id,
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        role=payload.role,
    )
    session.add(edge)
    await session.commit()
    await _enqueue_graph_sync(request, statement.project_id)
    return edge


@router.get("/{statement_id}/evidence", response_model=list[EvidenceOut])
async def list_evidence(
    statement_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[StatementEvidence]:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)
    rows = await session.scalars(
        select(StatementEvidence).where(StatementEvidence.statement_id == statement_id)
    )
    return list(rows)


@router.delete("/{statement_id}/evidence/{target_kind}/{target_id}", status_code=204)
async def remove_evidence(
    statement_id: uuid.UUID,
    target_kind: str,
    target_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)
    edge = await session.get(StatementEvidence, (statement_id, target_kind, target_id))
    if edge is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    await session.delete(edge)
    await session.commit()
    await _enqueue_graph_sync(request, statement.project_id)


# ── Edges: relates_to / supports / tension, between statements ────────────────

@router.post("/{statement_id}/edges", response_model=StatementEdgeOut, status_code=201)
async def add_edge(
    statement_id: uuid.UUID,
    payload: StatementEdgeCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> StatementEdge:
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)

    other = await _get_statement_or_404(session, payload.to_statement_id)
    if other.project_id != statement.project_id:
        raise HTTPException(status_code=400, detail="Target statement is in a different project")
    if other.id == statement_id:
        raise HTTPException(status_code=400, detail="A statement cannot relate to itself")

    existing = await session.get(StatementEdge, (statement_id, payload.to_statement_id))
    if existing is not None:
        existing.relation = payload.relation
        await session.commit()
        await _enqueue_graph_sync(request, statement.project_id)
        return existing

    edge = StatementEdge(
        from_statement_id=statement_id,
        to_statement_id=payload.to_statement_id,
        relation=payload.relation,
        created_by=user.id,
    )
    session.add(edge)
    await session.commit()
    await _enqueue_graph_sync(request, statement.project_id)
    return edge


@router.get("/{statement_id}/edges", response_model=list[StatementEdgeOut])
async def list_edges(
    statement_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[StatementEdge]:
    """Edges in either direction touching this statement."""
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)
    rows = await session.scalars(
        select(StatementEdge).where(
            (StatementEdge.from_statement_id == statement_id)
            | (StatementEdge.to_statement_id == statement_id)
        )
    )
    return list(rows)


@router.delete("/{statement_id}/edges/{other_id}", status_code=204)
async def remove_edge(
    statement_id: uuid.UUID,
    other_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> None:
    """Removes the edge whichever direction it was stored in (either
    statement_id -> other_id, or other_id -> statement_id)."""
    statement = await _get_statement_or_404(session, statement_id)
    await _assert_member(session, statement.project_id, user.id)

    edge = await session.get(StatementEdge, (statement_id, other_id))
    if edge is None:
        edge = await session.get(StatementEdge, (other_id, statement_id))
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found")
    await session.delete(edge)
    await session.commit()
    await _enqueue_graph_sync(request, statement.project_id)


# ── Project-scoped list ────────────────────────────────────────────────────────

@router.get("/by-project/{project_id}", response_model=list[StatementOut])
async def list_statements(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    type: str | None = Query(default=None),
) -> list[Statement]:
    await _assert_member(session, project_id, user.id)
    stmt = select(Statement).where(Statement.project_id == project_id)
    if type is not None:
        stmt = stmt.where(Statement.type == type)
    rows = await session.scalars(stmt.order_by(Statement.drafted_at.desc()))
    return list(rows)


@router.post("/by-project/{project_id}/lock-all", response_model=list[StatementOut])
async def lock_all_statements(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> list[Statement]:
    """Locks every not-yet-locked statement in the project, e.g. before export."""
    await _assert_member(session, project_id, user.id)
    rows = await session.scalars(
        select(Statement).where(
            Statement.project_id == project_id,
            Statement.status.notin_(["locked", "open", "resolved", "wontfix"]),
        )
    )
    statements = list(rows)
    now = datetime.now(timezone.utc)
    for statement in statements:
        statement.status = "locked"
        statement.locked_at = now
        _log_provenance(
            session,
            project_id,
            "statement_locked",
            {"statement_id": str(statement.id), "reason": "lock_all"},
        )
    await session.commit()
    for statement in statements:
        await session.refresh(statement)
    await _enqueue_graph_sync(request, project_id)
    return statements
