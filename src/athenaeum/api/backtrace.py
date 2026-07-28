"""Backtrace — walk a citation tree, one node at a time, starting from a
claim in a paper the user is reading. Resolving a node is two separate
phases: `/rank` asks the LLM which of the parent paper's own OpenAlex
references most plausibly support the same claim — advisory only, no DB row
created, so it's cheap to call repeatedly and never auto-commits to a guess
— and `/hops` commits one specific candidate a human picked, fetching its
metadata and resolving a verbatim quote for it (abstract first, escalating
to full-text ingestion when the abstract doesn't have one) as a real
BacktraceHop. A hop can itself later become the `parent_hop_id` of several
further hops, so a claim's several citations can be explored as parallel
branches instead of only ever continuing from "the last hop." See
synthesize/backtrace.py for the LLM/retrieval logic this composes.

Each of these calls runs synchronously inside its own request handler
(unlike synthesis, which is an arq job), so there's no persisted step log —
instead each mutating endpoint accepts an optional client-generated
`stream_id` and publishes ephemeral progress events to
`backtrace:stream:{stream_id}` as it goes, purely so the UI can show what's
happening right now. The actual result always comes from the endpoint's own
response; a client that never opens the stream, or whose stream drops a
message, loses nothing but the live status text. See the `/stream/{stream_id}`
websocket below."""
from __future__ import annotations

import asyncio
import json
import uuid

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import func, select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import (
    BacktraceCandidateOut,
    BacktraceChainDetailOut,
    BacktraceChainOut,
    BacktraceChainSummaryOut,
    BacktraceCommitRequest,
    BacktraceHopOut,
    BacktraceHopResult,
    BacktraceRankRequest,
    BacktraceRankResult,
    BacktraceStartRequest,
)
from athenaeum.ingest.openalex import fetch_metadata_by_id
from athenaeum.ingest.pipeline import backfill_openalex_id, find_or_create_target_paper
from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user
from athenaeum.security import decode_session_token
from athenaeum.storage.models import (
    BacktraceChain,
    BacktraceHop,
    Paper,
    PaperSentence,
    Project,
    ProjectMember,
)
from athenaeum.synthesize.backtrace import (
    RankedCandidate,
    extract_abstract_quote,
    identify_cited_work,
    resolve_reference_candidates,
    search_full_text_for_claim,
)

log = structlog.get_logger()
router = APIRouter(prefix="/backtrace", tags=["backtrace"])

_RESOLVED_STATES = {"abstract_match", "full_text_match"}


async def _publish_step(arq_pool: AsyncRedis | None, stream_id: str | None, kind: str) -> None:
    """Best-effort progress ping — see module docstring. Never raises: a
    dropped status update is cosmetic, never worth failing the request."""
    if arq_pool is None or stream_id is None:
        return
    try:
        await arq_pool.publish(f"backtrace:stream:{stream_id}", json.dumps({"kind": kind}))
    except Exception:
        log.warning("backtrace.publish_step_failed", stream_id=stream_id, kind=kind)


async def _require_member(session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")


def _names(authors: list[dict]) -> list[str]:
    return [a["name"] for a in authors if a.get("name")]


def _candidate_out(r: RankedCandidate) -> BacktraceCandidateOut:
    c = r.candidate
    return BacktraceCandidateOut(
        openalex_id=c.openalex_id, title=c.title, authors=c.authors, year=c.year,
        doi=c.doi, venue=c.venue, reasoning=r.reasoning, confident=r.confident,
    )


async def _load_chain_and_hops(
    session: SessionDep, chain_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[BacktraceChain, list[BacktraceHop]]:
    chain = await session.get(BacktraceChain, chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    await _require_member(session, chain.project_id, user_id)
    hops = list(
        await session.scalars(
            select(BacktraceHop)
            .where(BacktraceHop.chain_id == chain_id)
            .order_by(BacktraceHop.hop_index)
        )
    )
    return chain, hops


async def _resolve_parent(
    session: SessionDep,
    chain: BacktraceChain,
    hops: list[BacktraceHop],
    parent_hop_id: uuid.UUID | None,
) -> tuple[str, str, BacktraceHop | None]:
    """Returns (from_openalex_id, citing_text, parent_hop) for either the
    chain's origin paper (parent_hop_id=None) or an explicit existing hop —
    the single place both /rank and /hops derive "what am I tracing from"
    so a client-supplied parent_hop_id can't point outside this chain or at
    an unresolved hop."""
    if parent_hop_id is None:
        origin_openalex_id = await session.scalar(
            select(Paper.openalex_id).where(Paper.id == chain.origin_paper_id)
        )
        if not origin_openalex_id:
            raise HTTPException(status_code=422, detail="Origin paper has no OpenAlex record.")
        return origin_openalex_id, chain.origin_text, None

    hop = next((h for h in hops if h.id == parent_hop_id), None)
    if hop is None:
        raise HTTPException(status_code=404, detail="Hop not found in this chain.")
    if hop.resolution_status not in _RESOLVED_STATES:
        raise HTTPException(status_code=409, detail="That hop hasn't found supporting text yet.")
    if hop.quote is None:
        # Shouldn't happen — resolution_status in _RESOLVED_STATES is
        # supposed to imply a quote was found — but quote has no NOT NULL
        # constraint at the DB level, so guard rather than crash deeper in
        # a data-integrity edge case.
        raise HTTPException(status_code=409, detail="That hop has no quote text to trace from.")
    return hop.to_openalex_id, hop.quote, hop


async def _excluded_openalex_ids(
    session: SessionDep, chain: BacktraceChain, hops: list[BacktraceHop]
) -> set[str]:
    """No target paper should appear twice anywhere in one tree (mirrors
    the DB's own backtrace_hops_chain_target_uq constraint) — tree-wide,
    not just along the ancestor path to whichever hop is being extended."""
    excluded = {h.to_openalex_id for h in hops}
    origin_openalex_id = await session.scalar(
        select(Paper.openalex_id).where(Paper.id == chain.origin_paper_id)
    )
    if origin_openalex_id:
        excluded.add(origin_openalex_id)
    return excluded


@router.post("/chains", response_model=BacktraceChainOut, status_code=201)
async def start_chain(
    payload: BacktraceStartRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> BacktraceChainOut:
    await _require_member(session, payload.project_id, user.id)

    origin_paper = await session.get(Paper, payload.paper_id)
    if origin_paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    if not origin_paper.openalex_id:
        # Common for Zotero-imported papers, which never get matched to OpenAlex
        # at import time — try to resolve one now from the paper's own DOI
        # rather than failing outright.
        await backfill_openalex_id(origin_paper, session)
        if origin_paper.openalex_id:
            await session.commit()
        else:
            detail = (
                "Could not find an OpenAlex record for this paper's DOI."
                if origin_paper.doi
                else "This paper has no DOI, so it can't be matched to an OpenAlex record."
            )
            raise HTTPException(status_code=422, detail=detail)

    if payload.sentence_id is not None:
        sentence = await session.get(PaperSentence, payload.sentence_id)
        if sentence is None or sentence.paper_id != origin_paper.id:
            raise HTTPException(status_code=400, detail="Sentence does not belong to this paper")

    chain = BacktraceChain(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        created_by=user.id,
        origin_paper_id=origin_paper.id,
        origin_sentence_id=payload.sentence_id,
        origin_text=payload.claim_text.strip(),
    )
    session.add(chain)
    await session.commit()
    await session.refresh(chain)
    return BacktraceChainOut.model_validate(chain)


async def _rank(
    session: SessionDep,
    settings,
    provider,
    arq_pool,
    chain: BacktraceChain,
    hops: list[BacktraceHop],
    parent_hop_id: uuid.UUID | None,
    stream_id: str | None,
) -> BacktraceRankResult:
    from_openalex_id, citing_text, _parent = await _resolve_parent(
        session, chain, hops, parent_hop_id
    )

    await _publish_step(arq_pool, stream_id, "fetching_references")
    candidates = await resolve_reference_candidates(from_openalex_id)
    if not candidates:
        return BacktraceRankResult(
            candidates=[], message="This paper has no OpenAlex references to trace."
        )

    excluded = await _excluded_openalex_ids(session, chain, hops)
    available = [c for c in candidates if c.openalex_id not in excluded]
    if not available:
        return BacktraceRankResult(
            candidates=[],
            message="No new references to trace — every candidate is already part of this tree.",
        )

    await _publish_step(arq_pool, stream_id, "identifying_reference")
    identify = await identify_cited_work(provider, citing_text, available)
    return BacktraceRankResult(
        candidates=[_candidate_out(r) for r in identify.candidates],
        message=(
            None if identify.candidates
            else "No plausible candidates found among this paper's references."
        ),
        model=settings.zotero_find_model,
    )


@router.post("/chains/{chain_id}/rank", response_model=BacktraceRankResult)
async def rank_references(
    chain_id: uuid.UUID,
    payload: BacktraceRankRequest,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
    stream_id: str | None = Query(default=None),
) -> BacktraceRankResult:
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)

    settings = get_settings()
    try:
        provider = await get_provider_for_user(
            session, user.id, settings.zotero_find_model, settings
        )
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    arq_pool = getattr(request.app.state, "arq_pool", None)
    result = await _rank(
        session, settings, provider, arq_pool, chain, hops, payload.parent_hop_id, stream_id
    )
    await _publish_step(arq_pool, stream_id, "done")
    return result


async def _commit_hop(
    session: SessionDep,
    settings,
    provider,
    arq_pool,
    chain: BacktraceChain,
    hops: list[BacktraceHop],
    parent_hop_id: uuid.UUID | None,
    openalex_id: str,
    reasoning: str | None,
    user_id: uuid.UUID,
    stream_id: str | None,
) -> BacktraceHop:
    from_openalex_id, citing_text, parent_hop = await _resolve_parent(
        session, chain, hops, parent_hop_id
    )

    await _publish_step(arq_pool, stream_id, "fetching_references")
    candidates = await resolve_reference_candidates(from_openalex_id)
    excluded = await _excluded_openalex_ids(session, chain, hops)
    available = [c for c in candidates if c.openalex_id not in excluded]
    # Never trust client-supplied candidate metadata — re-derive and
    # re-validate the chosen id server-side.
    chosen = next((c for c in available if c.openalex_id == openalex_id), None)
    if chosen is None:
        raise HTTPException(
            status_code=400, detail="Not a valid, not-yet-visited reference of this paper"
        )

    hop_index = (parent_hop.hop_index + 1) if parent_hop is not None else 0

    await _publish_step(arq_pool, stream_id, "fetching_reference_details")
    loop = asyncio.get_running_loop()
    meta = await loop.run_in_executor(None, fetch_metadata_by_id, chosen.openalex_id)
    if meta is None:
        raise HTTPException(
            status_code=502, detail="Could not fetch details for the identified reference."
        )

    hop = BacktraceHop(
        id=uuid.uuid4(),
        chain_id=chain.id,
        parent_hop_id=parent_hop_id,
        hop_index=hop_index,
        to_openalex_id=chosen.openalex_id,
        to_doi=meta.doi,
        to_title=meta.title or chosen.title,
        to_authors_json=_names(meta.authors) or chosen.authors,
        to_year=meta.year or chosen.year,
        to_venue=meta.venue,
        match_reasoning=reasoning,
        model=settings.zotero_find_model,
        resolution_status="pending",
        user_decision="pending",
    )
    session.add(hop)
    await session.flush()

    await _publish_step(arq_pool, stream_id, "searching_abstract")
    abstract_result = await extract_abstract_quote(provider, citing_text, meta)
    if abstract_result is not None:
        hop.quote, hop.relevance_note = abstract_result
        hop.quote_source = "abstract"
        hop.resolution_status = "abstract_match"
        await session.commit()
        return hop

    # Abstract had nothing supporting the claim — escalate to full text.
    paper = await find_or_create_target_paper(session, chain.project_id, user_id, meta)
    hop.to_paper_id = paper.id

    if paper.status == "full_text":
        await _publish_step(arq_pool, stream_id, "searching_full_text")
        passage = await search_full_text_for_claim(
            provider, session, chain.project_id, paper.id, citing_text, settings.embed_model
        )
        if passage is not None:
            hop.quote, hop.relevance_note = passage.verbatim_text, passage.rationale
            hop.quote_source = "full_text"
            hop.resolution_status = "full_text_match"
        else:
            hop.resolution_status = "no_match"
        await session.commit()
        return hop

    if paper.status == "failed":
        hop.resolution_status = "awaiting_manual_import"
        await session.commit()
        return hop

    hop.resolution_status = "ingesting"
    await session.commit()
    if arq_pool is not None:
        try:
            await arq_pool.enqueue_job(
                "ingest_paper", str(paper.id),
                oa_pdf_url=meta.oa_pdf_url, backtrace_hop_id=str(hop.id),
            )
        except Exception:
            log.warning("backtrace.ingest_enqueue_failed", hop_id=str(hop.id))
    return hop


@router.post("/chains/{chain_id}/hops", response_model=BacktraceHopOut, status_code=201)
async def commit_hop(
    chain_id: uuid.UUID,
    payload: BacktraceCommitRequest,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
    stream_id: str | None = Query(default=None),
) -> BacktraceHopOut:
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)

    settings = get_settings()
    try:
        provider = await get_provider_for_user(
            session, user.id, settings.zotero_find_model, settings
        )
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    arq_pool = getattr(request.app.state, "arq_pool", None)
    hop = await _commit_hop(
        session, settings, provider, arq_pool, chain, hops,
        payload.parent_hop_id, payload.openalex_id, payload.reasoning, user.id, stream_id,
    )
    await _publish_step(arq_pool, stream_id, "done")
    return BacktraceHopOut.model_validate(hop)


@router.post("/chains/{chain_id}/hops/{hop_id}/stop", response_model=BacktraceHopOut)
async def stop_hop(
    chain_id: uuid.UUID, hop_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
) -> BacktraceHopOut:
    _chain, hops = await _load_chain_and_hops(session, chain_id, user.id)
    hop = next((h for h in hops if h.id == hop_id), None)
    if hop is None:
        raise HTTPException(status_code=404, detail="Hop not found")
    hop.user_decision = "stopped_here"
    await session.commit()
    await session.refresh(hop)
    return BacktraceHopOut.model_validate(hop)


@router.post("/chains/{chain_id}/hops/{hop_id}/recheck", response_model=BacktraceHopResult)
async def recheck_hop(
    chain_id: uuid.UUID,
    hop_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
    stream_id: str | None = Query(default=None),
) -> BacktraceHopResult:
    """Resume a hop stuck in 'awaiting_manual_import' after the user has
    supplied full text themselves (PDF upload or Zotero add-and-sync, both
    pre-existing app flows) for the paper this hop points at."""
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)
    hop = next((h for h in hops if h.id == hop_id), None)
    if hop is None:
        raise HTTPException(status_code=404, detail="Hop not found")

    arq_pool = getattr(request.app.state, "arq_pool", None)

    if hop.resolution_status not in ("awaiting_manual_import", "ingesting"):
        return BacktraceHopResult(hop=BacktraceHopOut.model_validate(hop))

    if hop.to_paper_id is None:
        raise HTTPException(status_code=409, detail="This hop has no linked paper to check.")
    paper = await session.get(Paper, hop.to_paper_id)
    if paper is None or paper.status != "full_text":
        status_word = paper.status if paper is not None else "unknown"
        return BacktraceHopResult(
            hop=BacktraceHopOut.model_validate(hop),
            message=f"Paper status: {status_word}. Not ready yet.",
        )

    settings = get_settings()
    try:
        provider = await get_provider_for_user(
            session, user.id, settings.zotero_find_model, settings
        )
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    # Re-derive what claim this hop is actually meant to support — its
    # parent's quote (or the chain's origin text at depth 0), not always
    # the origin text regardless of depth.
    _from_openalex_id, citing_text, _parent = await _resolve_parent(
        session, chain, hops, hop.parent_hop_id
    )

    await _publish_step(arq_pool, stream_id, "searching_full_text")
    passage = await search_full_text_for_claim(
        provider, session, chain.project_id, paper.id, citing_text, settings.embed_model
    )
    if passage is not None:
        hop.quote, hop.relevance_note = passage.verbatim_text, passage.rationale
        hop.quote_source = "full_text"
        hop.resolution_status = "full_text_match"
    else:
        hop.resolution_status = "no_match"
    await session.commit()
    await session.refresh(hop)
    await _publish_step(arq_pool, stream_id, "done")
    return BacktraceHopResult(hop=BacktraceHopOut.model_validate(hop))


@router.get("/chains/{chain_id}", response_model=BacktraceChainDetailOut)
async def get_chain(
    chain_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
) -> BacktraceChainDetailOut:
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)
    return BacktraceChainDetailOut(
        chain=BacktraceChainOut.model_validate(chain),
        hops=[BacktraceHopOut.model_validate(h) for h in hops],
    )


@router.get("/chains", response_model=list[BacktraceChainSummaryOut])
async def list_chains(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
    paper_id: uuid.UUID | None = Query(default=None),
) -> list[BacktraceChainSummaryOut]:
    await _require_member(session, project_id, user.id)

    stmt = select(BacktraceChain).where(BacktraceChain.project_id == project_id)
    if paper_id is not None:
        stmt = stmt.where(BacktraceChain.origin_paper_id == paper_id)
    chains = list(await session.scalars(stmt.order_by(BacktraceChain.created_at.desc())))
    if not chains:
        return []
    chain_ids = [c.id for c in chains]

    # hop_index already *is* depth from origin (see BacktraceHop.parent_hop_id),
    # so max_depth/has_pending are flat GROUP BY aggregates — no recursive
    # query needed.
    stat_rows = (
        await session.execute(
            select(
                BacktraceHop.chain_id,
                func.count(BacktraceHop.id),
                func.max(BacktraceHop.hop_index),
                func.bool_or(BacktraceHop.resolution_status.in_(("pending", "ingesting"))),
            )
            .where(BacktraceHop.chain_id.in_(chain_ids))
            .group_by(BacktraceHop.chain_id)
        )
    ).all()
    stats = {
        chain_id: (count, max_index, has_pending)
        for chain_id, count, max_index, has_pending in stat_rows
    }

    # A leaf is a hop that is nobody's parent.
    leaf_rows = (
        await session.execute(
            select(BacktraceHop.chain_id, func.count(BacktraceHop.id))
            .where(
                BacktraceHop.chain_id.in_(chain_ids),
                ~BacktraceHop.id.in_(
                    select(BacktraceHop.parent_hop_id).where(BacktraceHop.parent_hop_id.is_not(None))
                ),
            )
            .group_by(BacktraceHop.chain_id)
        )
    ).all()
    leaf_counts = {chain_id: count for chain_id, count in leaf_rows}

    return [
        BacktraceChainSummaryOut(
            id=c.id,
            origin_paper_id=c.origin_paper_id,
            origin_sentence_id=c.origin_sentence_id,
            origin_text=c.origin_text,
            hop_count=stats.get(c.id, (0, None, False))[0],
            max_depth=(stats.get(c.id, (0, None, False))[1] or -1) + 1,
            leaf_count=leaf_counts.get(c.id, 0),
            has_pending=bool(stats.get(c.id, (0, None, False))[2]),
            created_at=c.created_at,
        )
        for c in chains
    ]


# ── WebSocket — live step stream ─────────────────────────────────────────────
#
# Scoped to a client-generated `stream_id` rather than a chain_id: the very
# first hop is resolved inside the same request that creates the chain, so
# there's no chain_id a client could subscribe against beforehand. The client
# mints a UUID, opens this socket, waits for it to connect, and only then
# fires the mutating request with that id as `?stream_id=`. No DB-backed
# replay log exists (see module docstring) — a client that connects late
# simply sees fewer status pings, never stale or wrong ones.

@router.websocket("/stream/{stream_id}")
async def stream_backtrace_steps(
    websocket: WebSocket,
    stream_id: str,
    token: str = Query(..., description="JWT session token"),
) -> None:
    try:
        decode_session_token(token)
    except Exception:
        await websocket.close(code=4001, reason="invalid token")
        return

    settings = get_settings()
    await websocket.accept()

    redis_client = aioredis.from_url(settings.redis_url)
    pubsub = redis_client.pubsub()
    channel = f"backtrace:stream:{stream_id}"

    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            await websocket.send_text(data)
            if json.loads(data).get("kind") == "done":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws.backtrace_stream_error", stream_id=stream_id)
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        await redis_client.aclose()
