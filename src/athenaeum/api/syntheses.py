"""Synthesis endpoints: POST to start, GET result/steps, WS for live trace."""
from __future__ import annotations

import json
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import (
    EvaluationOut,
    SynthesisCreate,
    SynthesisEvidenceFullOut,
    SynthesisEvidenceOut,
    SynthesisOut,
    SynthesisStepOut,
    SynthesisUpdate,
)
from athenaeum.domain.authors import first_author_name
from athenaeum.provider.resolver import NoLLMKeyConfigured, resolve_api_key
from athenaeum.security import decode_session_token
from athenaeum.storage.db import get_sessionmaker
from athenaeum.storage.models import (
    Evaluation,
    Paper,
    Passage,
    ProjectMember,
    Synthesis,
    SynthesisEvidence,
    SynthesisStep,
    User,
)

log = structlog.get_logger()
router = APIRouter(prefix="/syntheses", tags=["syntheses"])


# ── REST ───────────────────────────────────────────────────────────────────────

@router.post("", response_model=SynthesisOut, status_code=201)
async def create_synthesis(
    payload: SynthesisCreate,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Synthesis:
    settings = get_settings()

    try:
        await resolve_api_key(session, user.id, settings)
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == payload.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    synthesis = Synthesis(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        claim_text=payload.claim_text,
        retrieval_corpus=payload.retrieval_corpus,
        model=settings.synthesis_model,
        status="running",
    )
    session.add(synthesis)
    await session.commit()
    await session.refresh(synthesis)

    try:
        arq_pool = request.app.state.arq_pool
        if arq_pool is None:
            raise RuntimeError("arq pool not available")
        await arq_pool.enqueue_job("run_synthesis_job", str(synthesis.id), str(user.id))
        log.info("synthesis.enqueued", synthesis_id=str(synthesis.id))
    except Exception as exc:
        synthesis.status = "failed"
        await session.commit()
        raise HTTPException(status_code=503, detail="Job queue unavailable") from exc

    return synthesis


@router.delete("/{synthesis_id}", status_code=204)
async def delete_synthesis(
    synthesis_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")

    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == synthesis.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    await session.delete(synthesis)
    await session.commit()


@router.patch("/{synthesis_id}", response_model=SynthesisOut)
async def update_synthesis(
    synthesis_id: uuid.UUID,
    payload: SynthesisUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Synthesis:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")

    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == synthesis.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    if payload.synthesis_text is not None:
        synthesis.synthesis_text = payload.synthesis_text
    if payload.query_expansion_json is not None:
        synthesis.query_expansion_json = payload.query_expansion_json

    await session.commit()
    await session.refresh(synthesis)
    return synthesis


async def _assert_synthesis_member(
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


@router.get("/{synthesis_id}", response_model=SynthesisOut)
async def get_synthesis(
    synthesis_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> Synthesis:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    await _assert_synthesis_member(session, synthesis.project_id, user.id)
    return synthesis


@router.get("/{synthesis_id}/steps", response_model=list[SynthesisStepOut])
async def get_steps(
    synthesis_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[SynthesisStep]:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    await _assert_synthesis_member(session, synthesis.project_id, user.id)

    rows = await session.scalars(
        select(SynthesisStep)
        .where(SynthesisStep.synthesis_id == synthesis_id)
        .order_by(SynthesisStep.ord)
    )
    return list(rows)


@router.get("/{synthesis_id}/evidence", response_model=list[SynthesisEvidenceOut])
async def get_evidence(
    synthesis_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[SynthesisEvidence]:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    await _assert_synthesis_member(session, synthesis.project_id, user.id)

    rows = await session.scalars(
        select(SynthesisEvidence)
        .where(SynthesisEvidence.synthesis_id == synthesis_id)
        .order_by(SynthesisEvidence.ord)
    )
    return list(rows)


@router.get("/{synthesis_id}/evidence-full", response_model=list[SynthesisEvidenceFullOut])
async def get_evidence_full(
    synthesis_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[SynthesisEvidenceFullOut]:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    await _assert_synthesis_member(session, synthesis.project_id, user.id)

    rows = await session.scalars(
        select(SynthesisEvidence)
        .where(SynthesisEvidence.synthesis_id == synthesis_id)
        .order_by(SynthesisEvidence.ord)
    )
    evidence_list = list(rows)
    if not evidence_list:
        return []

    passages = {
        p.id: p
        for p in await session.scalars(
            select(Passage).where(Passage.id.in_({ev.passage_id for ev in evidence_list}))
        )
    }
    papers = {
        p.id: p
        for p in await session.scalars(
            select(Paper).where(
                Paper.id.in_({pa.paper_id for pa in passages.values()})
            )
        )
    }

    result = []
    for ev in evidence_list:
        passage = passages.get(ev.passage_id)
        if passage is None:
            continue
        paper = papers.get(passage.paper_id)
        if paper is None:
            continue
        result.append(SynthesisEvidenceFullOut(
            synthesis_id=ev.synthesis_id,
            passage_id=ev.passage_id,
            role=ev.role,
            ord=ev.ord,
            verbatim_text=passage.verbatim_text,
            verified=passage.verified,
            paper_id=paper.id,
            paper_title=paper.title,
            paper_doi=paper.doi,
            paper_year=paper.year,
            paper_venue=paper.venue,
            paper_first_author=first_author_name(paper.authors_json),
            rationale=passage.rationale,
        ))
    return result


@router.get("/{synthesis_id}/evaluations", response_model=list[EvaluationOut])
async def list_evaluations(
    synthesis_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[Evaluation]:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    await _assert_synthesis_member(session, synthesis.project_id, user.id)

    rows = await session.scalars(
        select(Evaluation)
        .where(
            Evaluation.synthesis_id == synthesis_id,
            Evaluation.user_id == user.id,
        )
    )
    return list(rows)


# ── WebSocket — live trace stream ──────────────────────────────────────────────

@router.websocket("/{synthesis_id}/stream")
async def stream_synthesis(
    websocket: WebSocket,
    synthesis_id: uuid.UUID,
    token: str = Query(..., description="JWT session token"),
) -> None:
    """Stream synthesis step events as they are published.

    Protocol:
      - On connect: replays any steps already persisted to DB.
      - Then subscribes to Redis pub/sub for new steps.
      - Sends JSON objects: {kind, ord?, payload?}
      - Terminal messages: {kind: "done"} or {kind: "failed"}.
      - Client should also poll GET /syntheses/{id}/steps as the source of truth.
    """
    # Authenticate via query-param token (WebSocket headers can't carry Authorization).
    try:
        user_id = decode_session_token(token)
    except Exception:
        await websocket.close(code=4001, reason="invalid token")
        return

    settings = get_settings()
    await websocket.accept()

    redis_client = aioredis.from_url(settings.redis_url)
    pubsub = redis_client.pubsub()
    channel = f"synthesis:{synthesis_id}:steps"

    try:
        # Subscribe before reading DB to avoid missing events published in the window.
        await pubsub.subscribe(channel)

        maker = get_sessionmaker()
        async with maker() as session:
            # Verify user access (must be a member of the synthesis's project)
            synthesis = await session.get(Synthesis, synthesis_id)
            if synthesis is None:
                await websocket.close(code=4004, reason="synthesis not found")
                return

            membership = await session.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == synthesis.project_id,
                    ProjectMember.user_id == user_id,
                )
            )
            if membership is None:
                await websocket.close(code=4003, reason="forbidden")
                return

            # Replay already-persisted steps (catch-up for late connects).
            existing = await session.scalars(
                select(SynthesisStep)
                .where(SynthesisStep.synthesis_id == synthesis_id)
                .order_by(SynthesisStep.ord)
            )
            for step in existing:
                await websocket.send_text(
                    json.dumps({
                        "kind": step.kind,
                        "ord": step.ord,
                        "payload": step.payload_json,
                    })
                )

            # If already terminal, send the final message and close.
            if synthesis.status in ("done", "failed"):
                await websocket.send_text(json.dumps({"kind": synthesis.status}))
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
        log.exception("ws.stream_error", synthesis_id=str(synthesis_id))
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        await redis_client.aclose()
