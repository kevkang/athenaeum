"""Single point of entry for emitting project events.

Callers mutate ORM rows and then call apply_event() inside the same session
and commit. Routing all mutations through this function is the discipline that
keeps the event log faithful to actual state.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.storage.models import Event


async def apply_event(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    kind: str,
    entity_kind: str,
    entity_id: uuid.UUID | None,
    payload: dict,
    prev: dict | None = None,
) -> Event:
    """Allocate the next project_seq and add a new Event to the session.

    Caller owns the surrounding transaction — this does not commit.
    """
    seq_row = await session.execute(
        text(
            """
            INSERT INTO project_event_seq (project_id, next_seq)
            VALUES (:pid, 2)
            ON CONFLICT (project_id) DO UPDATE
                SET next_seq = project_event_seq.next_seq + 1
            RETURNING next_seq - 1 AS seq
            """
        ),
        {"pid": str(project_id)},
    )
    seq: int = seq_row.scalar_one()
    event = Event(
        id=uuid.uuid4(),
        project_id=project_id,
        project_seq=seq,
        actor_id=actor_id,
        kind=kind,
        entity_kind=entity_kind,
        entity_id=entity_id,
        prev_json=prev,
        payload_json=payload,
    )
    session.add(event)
    return event
