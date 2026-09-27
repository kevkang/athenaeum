"""Nucleate endpoints: nuclei, the canvas, prompts, expansions, reviews.

Expansions run as arq jobs (worker/jobs.py run_expansion_job). Progress is
persisted as events and published to Redis; this module replays and streams
them over a websocket, mirroring api/audit.py's stream, and also serves them
for polling. The persisted events are the source of truth; the stream is only
how they arrive quickly.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain import nucleate as v
from athenaeum.domain.models import (
    CanvasAuditOut,
    CanvasEvidenceOut,
    CanvasLineageOut,
    CanvasNodeOut,
    CanvasPaperOut,
    CanvasReviewOut,
    ExpansionDetailOut,
    ExpansionEventOut,
    ExpansionOut,
    NodeReviewUpdate,
    NucleusCreate,
    NucleusDetailOut,
    NucleusOut,
    NucleusPromptCreate,
    NucleusPromptOut,
)
from athenaeum.nucleate.corpus import member_node_ids
from athenaeum.nucleate.retrieval import author_names
from athenaeum.nucleate.review import InvalidReviewUpdate, apply_review_update
from athenaeum.security import decode_session_token
from athenaeum.storage.db import get_sessionmaker
from athenaeum.storage.models import (
    AuditFinding,
    AuditRun,
    CorpusNode,
    LineageEdge,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusExpansionEvent,
    NucleusPrompt,
    Paper,
    ProjectMember,
)

log = structlog.get_logger()
router = APIRouter(tags=["nucleate"])


def event_channel(expansion_id: uuid.UUID | str) -> str:
    """The Redis channel an expansion's events are published on."""
    return f"nucleate:{expansion_id}:events"


def is_terminal_event(event: dict) -> bool:
    """Whether an event ends the stream: the explicit awaiting-review state,
    or a stage-level failure. Per-node events never end it."""
    if event.get("node_id"):
        return False
    return (event.get("stage") == "awaiting_review" and event.get("status") == "completed") \
        or event.get("status") == "failed"


async def _assert_member(session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")


async def _nucleus_for(session: AsyncSession, nucleus_id: uuid.UUID, user_id: uuid.UUID) -> Nucleus:
    nucleus = await session.get(Nucleus, nucleus_id)
    if nucleus is None:
        raise HTTPException(status_code=404, detail="Nucleus not found")
    await _assert_member(session, nucleus.project_id, user_id)
    return nucleus


async def _start_expansion(
    session: AsyncSession, request: Request, nucleus: Nucleus, text: str,
    anchor_node_ids: list[uuid.UUID], budget: int, user_id: uuid.UUID,
) -> tuple[NucleusPrompt, NucleusExpansion]:
    """Record the prompt, create its run record, and enqueue the run."""
    prompt = NucleusPrompt(id=uuid.uuid4(), nucleus_id=nucleus.id, text=text,
                           anchor_node_ids=anchor_node_ids, created_by=user_id)
    session.add(prompt)
    await session.flush()
    expansion = NucleusExpansion(id=uuid.uuid4(), nucleus_id=nucleus.id, prompt_id=prompt.id,
                                 budget=budget, status=v.RUNNING)
    session.add(expansion)
    await session.commit()

    try:
        arq_pool = request.app.state.arq_pool
        if arq_pool is None:
            raise RuntimeError("arq pool not available")
        await arq_pool.enqueue_job("run_expansion_job", str(expansion.id), str(user_id))
        log.info("nucleate.enqueued", expansion_id=str(expansion.id))
    except Exception as exc:
        expansion.status = v.FAILED
        expansion.finished_at = datetime.now(UTC)
        await session.commit()
        raise HTTPException(status_code=503, detail="Job queue unavailable") from exc
    return prompt, expansion


# ── Building the canvas ──────────────────────────────────────────────────────


async def _latest_audits(
    session: AsyncSession, review_ids: list[uuid.UUID]
) -> dict[uuid.UUID, CanvasAuditOut]:
    """Each review's most recent automatic audit, derived rather than stored
    (D-N13). A statement counts as supported when any of its quotes supports
    it: each quote was chosen to support the statement on its own."""
    if not review_ids:
        return {}
    rows = (await session.execute(
        select(AuditFinding, AuditRun.created_at)
        .join(AuditRun, AuditRun.id == AuditFinding.audit_run_id)
        .where(AuditFinding.node_review_id.in_(review_ids))
        .order_by(AuditRun.created_at.desc(), AuditFinding.ord)
    )).all()
    latest_run: dict[uuid.UUID, uuid.UUID] = {}
    findings: dict[uuid.UUID, list[AuditFinding]] = {}
    checked_at: dict[uuid.UUID, datetime] = {}
    for finding, created in rows:
        review_id = finding.node_review_id
        assert review_id is not None
        if review_id not in latest_run:
            latest_run[review_id] = finding.audit_run_id
            checked_at[review_id] = created
        if finding.audit_run_id == latest_run[review_id]:
            findings.setdefault(review_id, []).append(finding)

    audits: dict[uuid.UUID, CanvasAuditOut] = {}
    for review_id, fs in findings.items():
        supporting = [f for f in fs if f.supported]
        chosen = supporting[0] if supporting else fs[0]
        audits[review_id] = CanvasAuditOut(
            supported=bool(supporting), rationale=chosen.rationale,
            checked_at=checked_at[review_id],
        )
    return audits


async def nucleus_detail(session: AsyncSession, nucleus: Nucleus) -> NucleusDetailOut:
    """Everything the canvas and the left panel need for one nucleus."""
    rows = (await session.execute(
        select(NodeReview, CorpusNode, Paper)
        .join(CorpusNode, CorpusNode.id == NodeReview.node_id)
        .join(Paper, Paper.id == CorpusNode.paper_id)
        .where(NodeReview.nucleus_id == nucleus.id)
        .order_by(NodeReview.created_at)
    )).all()
    review_ids = [r.id for r, _n, _p in rows]
    node_ids = [n.id for _r, n, _p in rows]

    evidence: dict[uuid.UUID, list[CanvasEvidenceOut]] = {}
    if review_ids:
        for e in await session.scalars(
            select(NodeEvidence).where(NodeEvidence.review_id.in_(review_ids))
            .order_by(NodeEvidence.ord)
        ):
            evidence.setdefault(e.review_id, []).append(CanvasEvidenceOut(
                id=e.id, ord=e.ord, source=e.source, verbatim_text=e.verbatim_text,
                text_quote_selector=e.text_quote_selector,
            ))

    other_nuclei: dict[uuid.UUID, set[uuid.UUID]] = {}
    if node_ids:
        for node_id, other in (await session.execute(
            select(LineageEdge.to_node_id, LineageEdge.nucleus_id)
            .where(LineageEdge.to_node_id.in_(node_ids), LineageEdge.nucleus_id != nucleus.id)
            .distinct()
        )).all():
            other_nuclei.setdefault(node_id, set()).add(other)

    audits = await _latest_audits(session, review_ids)
    nodes = [
        CanvasNodeOut(
            node_id=node.id,
            paper=CanvasPaperOut(id=paper.id, title=paper.title,
                                 authors=author_names(paper.authors_json), year=paper.year,
                                 venue=paper.venue, doi=paper.doi),
            fulltext=v.fulltext_state(paper.status, bool(paper.abstract)),
            review=CanvasReviewOut(
                id=review.id, status=review.status, rejection_reason=review.rejection_reason,
                relevance_text=review.relevance_text,
                relevance_provenance=review.relevance_provenance,
                selection_reasoning=review.selection_reasoning,
            ),
            evidence=evidence.get(review.id, []),
            audit=audits.get(review.id),
            other_nucleus_ids=sorted(other_nuclei.get(node.id, set())),
        )
        for review, node, paper in rows
    ]

    lineage = [CanvasLineageOut.model_validate(e) for e in await session.scalars(
        select(LineageEdge).where(LineageEdge.nucleus_id == nucleus.id)
        .order_by(LineageEdge.created_at)
    )]

    expansions = {
        e.prompt_id: e for e in await session.scalars(
            select(NucleusExpansion).where(NucleusExpansion.nucleus_id == nucleus.id)
        )
    }
    prompts = [
        NucleusPromptOut(
            id=p.id, text=p.text, anchor_node_ids=list(p.anchor_node_ids or []),
            created_at=p.created_at,
            expansion=ExpansionOut.model_validate(expansions[p.id]) if p.id in expansions else None,
        )
        for p in await session.scalars(
            select(NucleusPrompt).where(NucleusPrompt.nucleus_id == nucleus.id)
            .order_by(NucleusPrompt.created_at)
        )
    ]
    return NucleusDetailOut(nucleus=NucleusOut.model_validate(nucleus), nodes=nodes,
                            lineage=lineage, prompts=prompts)


# ── REST ─────────────────────────────────────────────────────────────────────


@router.post("/projects/{project_id}/nuclei", response_model=NucleusDetailOut, status_code=201)
async def create_nucleus(
    project_id: uuid.UUID, payload: NucleusCreate, session: SessionDep,
    user: CurrentUserDep, request: Request,
) -> NucleusDetailOut:
    """State a question. It becomes the nucleus and also its first prompt, so
    the archivist starts proposing papers straight away."""
    await _assert_member(session, project_id, user.id)
    nucleus = Nucleus(id=uuid.uuid4(), project_id=project_id,
                      question=payload.question.strip(), created_by=user.id)
    session.add(nucleus)
    await session.flush()
    await _start_expansion(session, request, nucleus, nucleus.question, [], payload.budget,
                           user.id)
    return await nucleus_detail(session, nucleus)


@router.get("/projects/{project_id}/nuclei", response_model=list[NucleusOut])
async def list_nuclei(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUserDep
) -> list[Nucleus]:
    """The nuclei in a project, oldest first, for the switcher."""
    await _assert_member(session, project_id, user.id)
    return list(await session.scalars(
        select(Nucleus).where(Nucleus.project_id == project_id).order_by(Nucleus.created_at)
    ))


@router.get("/nuclei/{nucleus_id}", response_model=NucleusDetailOut)
async def get_nucleus(
    nucleus_id: uuid.UUID, session: SessionDep, user: CurrentUserDep
) -> NucleusDetailOut:
    return await nucleus_detail(session, await _nucleus_for(session, nucleus_id, user.id))


@router.post("/nuclei/{nucleus_id}/prompts", response_model=NucleusPromptOut, status_code=201)
async def send_prompt(
    nucleus_id: uuid.UUID, payload: NucleusPromptCreate, session: SessionDep,
    user: CurrentUserDep, request: Request,
) -> NucleusPromptOut:
    """Direct the next step, optionally anchored to nodes on the canvas."""
    nucleus = await _nucleus_for(session, nucleus_id, user.id)

    running = await session.scalar(
        select(NucleusExpansion.id).where(
            NucleusExpansion.nucleus_id == nucleus.id, NucleusExpansion.status == v.RUNNING
        )
    )
    if running is not None:
        # Two archivists writing one nucleus at once could race on the same
        # paper; one step at a time is also what the stage list can show.
        raise HTTPException(status_code=409,
                            detail="The archivist is still working on the last step")

    anchors = list(dict.fromkeys(payload.anchor_node_ids))
    if anchors and not set(anchors) <= await member_node_ids(session, nucleus.id):
        raise HTTPException(status_code=400, detail="Anchors must be nodes in this nucleus")

    prompt, expansion = await _start_expansion(
        session, request, nucleus, payload.text.strip(), anchors, payload.budget, user.id
    )
    return NucleusPromptOut(id=prompt.id, text=prompt.text,
                            anchor_node_ids=list(prompt.anchor_node_ids or []),
                            created_at=prompt.created_at,
                            expansion=ExpansionOut.model_validate(expansion))


async def _expansion_for(
    session: AsyncSession, expansion_id: uuid.UUID, user_id: uuid.UUID
) -> NucleusExpansion:
    expansion = await session.get(NucleusExpansion, expansion_id)
    if expansion is None:
        raise HTTPException(status_code=404, detail="Expansion not found")
    await _nucleus_for(session, expansion.nucleus_id, user_id)
    return expansion


@router.get("/expansions/{expansion_id}", response_model=ExpansionDetailOut)
async def get_expansion(
    expansion_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
    after: int = Query(default=-1, description="Only events with ord greater than this"),
) -> ExpansionDetailOut:
    """The run record and its events — the source of truth the stream mirrors,
    and the polling fallback."""
    expansion = await _expansion_for(session, expansion_id, user.id)
    events = await session.scalars(
        select(NucleusExpansionEvent)
        .where(NucleusExpansionEvent.expansion_id == expansion.id,
               NucleusExpansionEvent.ord > after)
        .order_by(NucleusExpansionEvent.ord)
    )
    return ExpansionDetailOut(expansion=ExpansionOut.model_validate(expansion),
                              events=[ExpansionEventOut.model_validate(e) for e in events])


@router.post("/expansions/{expansion_id}/cancel", response_model=ExpansionOut)
async def cancel_expansion(
    expansion_id: uuid.UUID, session: SessionDep, user: CurrentUserDep
) -> NucleusExpansion:
    """Ask a running expansion to stop. The worker checks between stages and
    between papers, and keeps the nodes it has already completed."""
    expansion = await _expansion_for(session, expansion_id, user.id)
    if expansion.status != v.RUNNING:
        raise HTTPException(status_code=409, detail=f"Expansion is already {expansion.status}")
    expansion.status = v.CANCELLED
    await session.commit()
    await session.refresh(expansion)
    return expansion


@router.patch("/node-reviews/{review_id}", response_model=CanvasReviewOut)
async def update_review(
    review_id: uuid.UUID, payload: NodeReviewUpdate, session: SessionDep, user: CurrentUserDep
) -> CanvasReviewOut:
    """Accept, reject, pin, or edit the relevance statement."""
    review = await session.get(NodeReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found")
    await _nucleus_for(session, review.nucleus_id, user.id)
    try:
        apply_review_update(review, status=payload.status,
                            rejection_reason=payload.rejection_reason,
                            relevance_text=payload.relevance_text)
    except InvalidReviewUpdate as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(review)
    return CanvasReviewOut(
        id=review.id, status=review.status, rejection_reason=review.rejection_reason,
        relevance_text=review.relevance_text, relevance_provenance=review.relevance_provenance,
        selection_reasoning=review.selection_reasoning,
    )


# ── WebSocket ────────────────────────────────────────────────────────────────


@router.websocket("/expansions/{expansion_id}/stream")
async def stream_expansion(
    websocket: WebSocket,
    expansion_id: uuid.UUID,
    token: str = Query(..., description="JWT session token"),
) -> None:
    """Stream an expansion's events as they happen.

    Mirrors api/audit.py's stream: subscribe first so nothing published during
    the replay is missed, replay the persisted events, then forward live ones
    until a terminal event. Each message is one event, in the same shape as
    GET /expansions/{id} returns them.
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
    channel = event_channel(expansion_id)

    try:
        await pubsub.subscribe(channel)
        async with get_sessionmaker()() as session:
            expansion = await session.get(NucleusExpansion, expansion_id)
            if expansion is None:
                await websocket.close(code=4004, reason="expansion not found")
                return
            nucleus = await session.get(Nucleus, expansion.nucleus_id)
            assert nucleus is not None
            member = await session.scalar(select(ProjectMember).where(
                ProjectMember.project_id == nucleus.project_id, ProjectMember.user_id == user_id))
            if member is None:
                await websocket.close(code=4003, reason="forbidden")
                return

            last_ord = -1
            for e in await session.scalars(
                select(NucleusExpansionEvent)
                .where(NucleusExpansionEvent.expansion_id == expansion_id)
                .order_by(NucleusExpansionEvent.ord)
            ):
                payload = ExpansionEventOut.model_validate(e).model_dump(mode="json")
                await websocket.send_text(json.dumps(payload))
                last_ord = e.ord
                if is_terminal_event(payload):
                    return
            if expansion.status != v.RUNNING:
                return

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            event = json.loads(data)
            # Already sent during the replay: the subscription overlaps it.
            if event.get("ord", -1) <= last_ord:
                continue
            await websocket.send_text(data)
            if is_terminal_event(event):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("nucleate.stream_error", expansion_id=str(expansion_id))
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        await redis_client.aclose()
