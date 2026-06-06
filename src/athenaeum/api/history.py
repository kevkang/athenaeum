"""Project event history and undo."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import EventOut, HistoryPage
from athenaeum.events import apply_event
from athenaeum.storage.models import Brick, Event, ProjectMember, User

router = APIRouter(prefix="/projects", tags=["history"])


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
            pass


@router.get("/{project_id}/history", response_model=HistoryPage)
async def list_history(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> HistoryPage:
    await _assert_member(session, project_id, user.id)

    stmt = (
        select(Event, User.display_name)
        .outerjoin(User, User.id == Event.actor_id)
        .where(Event.project_id == project_id)
    )
    if before_seq is not None:
        stmt = stmt.where(Event.project_seq < before_seq)
    stmt = stmt.order_by(Event.project_seq.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    events = [
        EventOut(
            id=ev.id,
            project_seq=ev.project_seq,
            actor_id=ev.actor_id,
            actor_display_name=display_name,
            kind=ev.kind,
            entity_kind=ev.entity_kind,
            entity_id=ev.entity_id,
            prev_json=ev.prev_json,
            payload_json=ev.payload_json,
            created_at=ev.created_at,
        )
        for ev, display_name in rows
    ]
    next_before = events[-1].project_seq if has_more and events else None
    return HistoryPage(events=events, next_before_seq=next_before)


@router.post("/{project_id}/events/{seq}/undo", response_model=EventOut)
async def undo_event(
    project_id: uuid.UUID,
    seq: int,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> EventOut:
    await _assert_member(session, project_id, user.id)

    event = await session.scalar(
        select(Event).where(
            Event.project_id == project_id,
            Event.project_seq == seq,
        )
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.kind != "brick.edited":
        raise HTTPException(
            status_code=400,
            detail=f"Undo not supported for events of kind {event.kind!r}",
        )
    if event.prev_json is None or event.entity_id is None:
        raise HTTPException(status_code=400, detail="Event has no pre-image")

    brick = await session.get(Brick, event.entity_id)
    if brick is None:
        raise HTTPException(
            status_code=409,
            detail="Target brick no longer exists",
        )

    # Stale check: refuse if anything in the entity has changed since this event.
    payload = event.payload_json
    if (
        brick.assertion != payload.get("assertion")
        or brick.type != payload.get("type")
        or brick.status != payload.get("status")
    ):
        raise HTTPException(
            status_code=409,
            detail="Brick has been modified since this event; undo is no longer safe",
        )

    # Capture current state as the new pre-image, then restore.
    current = {
        "assertion": brick.assertion,
        "type": brick.type,
        "status": brick.status,
        "cemented_at": brick.cemented_at.isoformat() if brick.cemented_at else None,
    }
    prev = event.prev_json
    brick.assertion = prev["assertion"]
    brick.type = prev["type"]
    brick.status = prev["status"]
    cemented_at = prev.get("cemented_at")
    brick.cemented_at = (
        datetime.fromisoformat(cemented_at) if cemented_at else None
    )

    new_event = await apply_event(
        session,
        project_id=project_id,
        actor_id=user.id,
        kind="brick.edited",
        entity_kind="brick",
        entity_id=brick.id,
        prev=current,
        payload={
            "assertion": brick.assertion,
            "type": brick.type,
            "status": brick.status,
            "undoes_seq": seq,
        },
    )
    await session.commit()
    await session.refresh(new_event)
    await _enqueue_graph_sync(request, project_id)

    return EventOut(
        id=new_event.id,
        project_seq=new_event.project_seq,
        actor_id=new_event.actor_id,
        actor_display_name=user.display_name,
        kind=new_event.kind,
        entity_kind=new_event.entity_kind,
        entity_id=new_event.entity_id,
        prev_json=new_event.prev_json,
        payload_json=new_event.payload_json,
        created_at=new_event.created_at,
    )
