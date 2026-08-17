"""Audit mode endpoints: POST to start (nested under compose-document), GET
result, WS for live findings — mirrors api/syntheses.py's shape almost
exactly (see synthesize/audit.py's module docstring for what's different)."""
from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.compose import _assert_member, get_or_create
from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.citations import build_reference_numbering, iter_statement_ids_in_order
from athenaeum.domain.models import AuditFindingOut, AuditRunDetailOut, AuditRunOut
from athenaeum.security import decode_session_token
from athenaeum.storage.db import get_sessionmaker
from athenaeum.storage.models import AuditFinding, AuditRun, ComposeDocument, ProjectMember

log = structlog.get_logger()
router = APIRouter(tags=["audit"])


# ── REST ───────────────────────────────────────────────────────────────────────

@router.post(
    "/projects/{project_id}/compose-document/audit",
    response_model=AuditRunOut,
    status_code=201,
)
async def create_audit_run(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> AuditRun:
    await _assert_member(session, project_id, user.id)
    doc = await get_or_create(session, project_id)

    audit_run = AuditRun(
        id=uuid.uuid4(),
        project_id=project_id,
        compose_document_id=doc.id,
        created_by=user.id,
        status="running",
    )
    session.add(audit_run)
    await session.commit()
    await session.refresh(audit_run)

    try:
        arq_pool = request.app.state.arq_pool
        if arq_pool is None:
            raise RuntimeError("arq pool not available")
        await arq_pool.enqueue_job("run_audit_job", str(audit_run.id), str(user.id))
        log.info("audit.enqueued", audit_run_id=str(audit_run.id))
    except Exception as exc:
        audit_run.status = "failed"
        await session.commit()
        raise HTTPException(status_code=503, detail="Job queue unavailable") from exc

    return audit_run


async def _assert_audit_member(
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


async def _statements_with_no_evidence(
    session: AsyncSession, compose_document_id: uuid.UUID
) -> list[uuid.UUID]:
    doc = await session.get(ComposeDocument, compose_document_id)
    if doc is None:
        return []
    statement_ids = iter_statement_ids_in_order(doc.content_json)
    numbering = await build_reference_numbering(session, statement_ids)
    return [sid for sid in statement_ids if not numbering.statement_markers.get(sid)]


@router.get("/audit-runs/{audit_run_id}", response_model=AuditRunDetailOut)
async def get_audit_run(
    audit_run_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> AuditRunDetailOut:
    audit_run = await session.get(AuditRun, audit_run_id)
    if audit_run is None:
        raise HTTPException(status_code=404, detail="Audit run not found")
    await _assert_audit_member(session, audit_run.project_id, user.id)

    rows = await session.scalars(
        select(AuditFinding)
        .where(AuditFinding.audit_run_id == audit_run_id)
        .order_by(AuditFinding.ord)
    )

    return AuditRunDetailOut(
        run=AuditRunOut.model_validate(audit_run),
        findings=[AuditFindingOut.model_validate(f) for f in rows],
        statements_with_no_evidence=await _statements_with_no_evidence(
            session, audit_run.compose_document_id
        ),
    )


# ── WebSocket — live findings stream ────────────────────────────────────────────

@router.websocket("/audit-runs/{audit_run_id}/stream")
async def stream_audit_run(
    websocket: WebSocket,
    audit_run_id: uuid.UUID,
    token: str = Query(..., description="JWT session token"),
) -> None:
    """Stream audit finding events as they are published.

    Protocol mirrors api/syntheses.py's stream_synthesis:
      - On connect: replays any findings already persisted to DB.
      - Then subscribes to Redis pub/sub for new findings.
      - Sends JSON objects: {kind, ord?, payload?}
      - Terminal messages: {kind: "done"} or {kind: "failed"}.
      - Client should also poll GET /audit-runs/{id} as the source of truth.
    """
    try:
        user_id = decode_session_token(token)
    except Exception:
        await websocket.close(code=4001, reason="invalid token")
        return

    settings = get_settings()
    await websocket.accept()

    redis_client = aioredis.from_url(settings.redis_url)
    pubsub = redis_client.pubsub()
    channel = f"audit:{audit_run_id}:steps"

    try:
        # Subscribe before reading DB to avoid missing events published in the window.
        await pubsub.subscribe(channel)

        maker = get_sessionmaker()
        async with maker() as session:
            audit_run = await session.get(AuditRun, audit_run_id)
            if audit_run is None:
                await websocket.close(code=4004, reason="audit run not found")
                return

            membership = await session.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == audit_run.project_id,
                    ProjectMember.user_id == user_id,
                )
            )
            if membership is None:
                await websocket.close(code=4003, reason="forbidden")
                return

            # Replay already-persisted findings (catch-up for late connects).
            existing = await session.scalars(
                select(AuditFinding)
                .where(AuditFinding.audit_run_id == audit_run_id)
                .order_by(AuditFinding.ord)
            )
            for finding in existing:
                await websocket.send_text(
                    json.dumps({
                        "kind": "finding",
                        "ord": finding.ord,
                        "payload": {
                            "statement_id": str(finding.statement_id),
                            "sub_id": finding.sub_id,
                            "target_kind": finding.target_kind,
                            "target_id": str(finding.target_id),
                            "quote": finding.quote,
                            "supported": finding.supported,
                            "rationale": finding.rationale,
                        },
                    })
                )

            # If already terminal, send the final message and close.
            if audit_run.status in ("done", "failed"):
                await websocket.send_text(json.dumps({"kind": audit_run.status}))
                return

        # Forward live events from Redis.
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            await websocket.send_text(data)
            parsed = json.loads(data)
            if parsed.get("kind") in ("done", "failed"):
                break

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws.stream_error", audit_run_id=str(audit_run_id))
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        await redis_client.aclose()
