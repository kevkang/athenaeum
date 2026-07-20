"""Backtrace — walk a citation chain one hop at a time, starting from a claim
in a paper the user is reading. Each hop identifies which of that paper's own
OpenAlex references most plausibly supports the same claim, finds a verbatim
quote for it (abstract first, escalating to full-text ingestion when the
abstract doesn't have one), and lets the user decide to trace further or stop.
See synthesize/backtrace.py for the LLM/retrieval logic this composes."""
from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import (
    BacktraceCandidateOut,
    BacktraceChainDetailOut,
    BacktraceChainOut,
    BacktraceChainSummaryOut,
    BacktracePickRequest,
    BacktraceHopOut,
    BacktraceStartRequest,
    BacktraceStepResult,
)
from athenaeum.ingest.openalex import OpenAlexMeta, fetch_metadata_by_id
from athenaeum.ingest.pipeline import backfill_openalex_id
from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user
from athenaeum.storage.models import (
    BacktraceChain,
    BacktraceHop,
    Paper,
    PaperSentence,
    Project,
    ProjectMember,
    ProjectPaper,
)
from athenaeum.synthesize.backtrace import (
    ReferenceCandidate,
    extract_abstract_quote,
    identify_cited_work,
    resolve_reference_candidates,
    search_full_text_for_claim,
)

log = structlog.get_logger()
router = APIRouter(prefix="/backtrace", tags=["backtrace"])

_RESOLVED_STATES = {"abstract_match", "full_text_match"}


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


async def _load_chain_and_hops(
    session: SessionDep, chain_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[BacktraceChain, list[BacktraceHop]]:
    chain = await session.get(BacktraceChain, chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="Chain not found")
    await _require_member(session, chain.project_id, user_id)
    hops = list(
        await session.scalars(
            select(BacktraceHop).where(BacktraceHop.chain_id == chain_id).order_by(BacktraceHop.hop_index)
        )
    )
    return chain, hops


async def _find_or_create_target_paper(
    session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID, meta: OpenAlexMeta
) -> Paper:
    existing = None
    if meta.doi:
        existing = await session.scalar(select(Paper).where(Paper.doi == meta.doi))
    if existing is None and meta.openalex_id:
        existing = await session.scalar(select(Paper).where(Paper.openalex_id == meta.openalex_id))

    if existing is not None:
        already_linked = await session.scalar(
            select(ProjectPaper).where(
                ProjectPaper.project_id == project_id, ProjectPaper.paper_id == existing.id
            )
        )
        if already_linked is None:
            session.add(ProjectPaper(project_id=project_id, paper_id=existing.id, added_by=user_id))
            await session.flush()
        return existing

    paper = Paper(
        id=uuid.uuid4(),
        doi=meta.doi,
        openalex_id=meta.openalex_id,
        title=meta.title,
        authors_json=meta.authors,
        venue=meta.venue,
        year=meta.year,
        abstract=meta.abstract,
        status="ghost",
    )
    session.add(paper)
    session.add(ProjectPaper(project_id=project_id, paper_id=paper.id, added_by=user_id))
    await session.flush()
    return paper


async def _resolve_hop(
    session: SessionDep,
    settings,
    provider,
    arq_pool,
    chain: BacktraceChain,
    hop_index: int,
    from_openalex_id: str,
    citing_text: str,
    user_id: uuid.UUID,
    manual_openalex_id: str | None,
) -> tuple[BacktraceHop | None, list[ReferenceCandidate] | None, str | None]:
    loop = asyncio.get_running_loop()
    candidates = await resolve_reference_candidates(from_openalex_id)
    if not candidates:
        return None, None, "This paper has no OpenAlex references to trace."

    origin_openalex_id = await session.scalar(
        select(Paper.openalex_id).where(Paper.id == chain.origin_paper_id)
    )
    excluded = set(
        await session.scalars(
            select(BacktraceHop.to_openalex_id).where(BacktraceHop.chain_id == chain.id)
        )
    )
    if origin_openalex_id:
        excluded.add(origin_openalex_id)
    available = [c for c in candidates if c.openalex_id not in excluded]
    if not available:
        return None, None, "No new references to trace — every candidate is already part of this chain."

    if manual_openalex_id is not None:
        chosen = next((c for c in available if c.openalex_id == manual_openalex_id), None)
        if chosen is None:
            raise HTTPException(
                status_code=400, detail="Not a valid, not-yet-visited reference of this paper"
            )
        reasoning = "Manually selected by user."
    else:
        identify = await identify_cited_work(provider, citing_text, available)
        if identify.candidate is None or not identify.confident:
            return None, available, identify.reasoning or "No confident citation match found."
        chosen = identify.candidate
        reasoning = identify.reasoning

    meta = await loop.run_in_executor(None, fetch_metadata_by_id, chosen.openalex_id)
    if meta is None:
        return None, None, "Could not fetch details for the identified reference."

    hop = BacktraceHop(
        id=uuid.uuid4(),
        chain_id=chain.id,
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

    abstract_result = await extract_abstract_quote(provider, chain.origin_text, meta)
    if abstract_result is not None:
        hop.quote, hop.relevance_note = abstract_result
        hop.quote_source = "abstract"
        hop.resolution_status = "abstract_match"
        await session.commit()
        return hop, None, None

    # Abstract had nothing supporting the claim — escalate to full text.
    paper = await _find_or_create_target_paper(session, chain.project_id, user_id, meta)
    hop.to_paper_id = paper.id

    if paper.status == "full_text":
        passage = await search_full_text_for_claim(
            provider, session, chain.project_id, paper.id, chain.origin_text, settings.embed_model
        )
        if passage is not None:
            hop.quote, hop.relevance_note = passage.verbatim_text, passage.rationale
            hop.quote_source = "full_text"
            hop.resolution_status = "full_text_match"
        else:
            hop.resolution_status = "no_match"
        await session.commit()
        return hop, None, None

    if paper.status == "failed":
        hop.resolution_status = "awaiting_manual_import"
        await session.commit()
        return hop, None, None

    hop.resolution_status = "ingesting"
    await session.commit()
    if arq_pool is not None:
        try:
            await arq_pool.enqueue_job(
                "ingest_paper", str(paper.id), oa_pdf_url=meta.oa_pdf_url, backtrace_hop_id=str(hop.id)
            )
        except Exception:
            log.warning("backtrace.ingest_enqueue_failed", hop_id=str(hop.id))
    return hop, None, None


def _hop_out(hop: BacktraceHop | None) -> BacktraceHopOut | None:
    return BacktraceHopOut.model_validate(hop) if hop is not None else None


def _step_result(
    chain: BacktraceChain,
    hop: BacktraceHop | None,
    candidates: list[ReferenceCandidate] | None,
    message: str | None,
    settings,
) -> BacktraceStepResult:
    # `message` is LLM-authored (identify_cited_work's stated reasoning)
    # precisely when candidates came back non-None — see _resolve_hop.
    return BacktraceStepResult(
        chain=BacktraceChainOut.model_validate(chain),
        hop=_hop_out(hop),
        candidates=_candidates_out(candidates),
        message=message,
        model=settings.zotero_find_model if candidates is not None else None,
    )


def _candidates_out(candidates: list[ReferenceCandidate] | None) -> list[BacktraceCandidateOut] | None:
    if candidates is None:
        return None
    return [
        BacktraceCandidateOut(
            openalex_id=c.openalex_id, title=c.title, authors=c.authors, year=c.year, doi=c.doi, venue=c.venue
        )
        for c in candidates
    ]


@router.post("/chains", response_model=BacktraceStepResult, status_code=201)
async def start_chain(
    payload: BacktraceStartRequest, session: SessionDep, user: CurrentUserDep, request: Request
) -> BacktraceStepResult:
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

    settings = get_settings()
    try:
        provider = await get_provider_for_user(session, user.id, settings.zotero_find_model, settings)
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    chain = BacktraceChain(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        created_by=user.id,
        origin_paper_id=origin_paper.id,
        origin_sentence_id=payload.sentence_id,
        origin_text=payload.claim_text.strip(),
    )
    session.add(chain)
    await session.flush()

    arq_pool = getattr(request.app.state, "arq_pool", None)
    hop, candidates, message = await _resolve_hop(
        session=session,
        settings=settings,
        provider=provider,
        arq_pool=arq_pool,
        chain=chain,
        hop_index=0,
        from_openalex_id=origin_paper.openalex_id,
        citing_text=chain.origin_text,
        user_id=user.id,
        manual_openalex_id=None,
    )
    await session.commit()
    await session.refresh(chain)

    return _step_result(chain, hop, candidates, message, settings)


async def _continue_or_pick(
    session: SessionDep, user: CurrentUserDep, request: Request, chain_id: uuid.UUID, manual_openalex_id: str | None
) -> BacktraceStepResult:
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)

    if hops:
        last = hops[-1]
        if last.user_decision == "stopped_here":
            raise HTTPException(status_code=409, detail="This chain was stopped at the last hop.")
        if last.resolution_status not in _RESOLVED_STATES:
            raise HTTPException(
                status_code=409, detail="The latest hop hasn't found supporting text yet."
            )
        last.user_decision = "continued"
        hop_index, from_openalex_id, citing_text = len(hops), last.to_openalex_id, last.quote
    else:
        origin_openalex_id = await session.scalar(
            select(Paper.openalex_id).where(Paper.id == chain.origin_paper_id)
        )
        if not origin_openalex_id:
            raise HTTPException(status_code=422, detail="Origin paper has no OpenAlex record.")
        hop_index, from_openalex_id, citing_text = 0, origin_openalex_id, chain.origin_text

    settings = get_settings()
    try:
        provider = await get_provider_for_user(session, user.id, settings.zotero_find_model, settings)
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    arq_pool = getattr(request.app.state, "arq_pool", None)
    hop, candidates, message = await _resolve_hop(
        session=session,
        settings=settings,
        provider=provider,
        arq_pool=arq_pool,
        chain=chain,
        hop_index=hop_index,
        from_openalex_id=from_openalex_id,
        citing_text=citing_text,
        user_id=user.id,
        manual_openalex_id=manual_openalex_id,
    )
    await session.commit()

    return _step_result(chain, hop, candidates, message, settings)


@router.post("/chains/{chain_id}/continue", response_model=BacktraceStepResult)
async def continue_chain(
    chain_id: uuid.UUID, session: SessionDep, user: CurrentUserDep, request: Request
) -> BacktraceStepResult:
    return await _continue_or_pick(session, user, request, chain_id, manual_openalex_id=None)


@router.post("/chains/{chain_id}/pick", response_model=BacktraceStepResult)
async def pick_hop(
    chain_id: uuid.UUID,
    payload: BacktracePickRequest,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> BacktraceStepResult:
    return await _continue_or_pick(session, user, request, chain_id, manual_openalex_id=payload.openalex_id)


@router.post("/chains/{chain_id}/stop", response_model=BacktraceChainDetailOut)
async def stop_chain(chain_id: uuid.UUID, session: SessionDep, user: CurrentUserDep) -> BacktraceChainDetailOut:
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)
    if not hops:
        raise HTTPException(status_code=400, detail="No hops to stop at.")
    hops[-1].user_decision = "stopped_here"
    await session.commit()
    return BacktraceChainDetailOut(
        chain=BacktraceChainOut.model_validate(chain), hops=[BacktraceHopOut.model_validate(h) for h in hops]
    )


@router.post("/chains/{chain_id}/hops/{hop_id}/recheck", response_model=BacktraceStepResult)
async def recheck_hop(
    chain_id: uuid.UUID, hop_id: uuid.UUID, session: SessionDep, user: CurrentUserDep
) -> BacktraceStepResult:
    """Resume a hop stuck in 'awaiting_manual_import' after the user has
    supplied full text themselves (PDF upload or Zotero add-and-sync, both
    pre-existing app flows) for the paper this hop points at."""
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)
    hop = next((h for h in hops if h.id == hop_id), None)
    if hop is None:
        raise HTTPException(status_code=404, detail="Hop not found")

    if hop.resolution_status not in ("awaiting_manual_import", "ingesting"):
        return BacktraceStepResult(chain=BacktraceChainOut.model_validate(chain), hop=_hop_out(hop))

    if hop.to_paper_id is None:
        raise HTTPException(status_code=409, detail="This hop has no linked paper to check.")
    paper = await session.get(Paper, hop.to_paper_id)
    if paper is None or paper.status != "full_text":
        status_word = paper.status if paper is not None else "unknown"
        return BacktraceStepResult(
            chain=BacktraceChainOut.model_validate(chain),
            hop=_hop_out(hop),
            message=f"Paper status: {status_word}. Not ready yet.",
        )

    settings = get_settings()
    try:
        provider = await get_provider_for_user(session, user.id, settings.zotero_find_model, settings)
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    passage = await search_full_text_for_claim(
        provider, session, chain.project_id, paper.id, chain.origin_text, settings.embed_model
    )
    if passage is not None:
        hop.quote, hop.relevance_note = passage.verbatim_text, passage.rationale
        hop.quote_source = "full_text"
        hop.resolution_status = "full_text_match"
    else:
        hop.resolution_status = "no_match"
    await session.commit()
    await session.refresh(hop)
    return BacktraceStepResult(chain=BacktraceChainOut.model_validate(chain), hop=_hop_out(hop))


@router.get("/chains/{chain_id}", response_model=BacktraceChainDetailOut)
async def get_chain(chain_id: uuid.UUID, session: SessionDep, user: CurrentUserDep) -> BacktraceChainDetailOut:
    chain, hops = await _load_chain_and_hops(session, chain_id, user.id)
    return BacktraceChainDetailOut(
        chain=BacktraceChainOut.model_validate(chain), hops=[BacktraceHopOut.model_validate(h) for h in hops]
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

    count_rows = (
        await session.execute(
            select(BacktraceHop.chain_id, func.count(BacktraceHop.id))
            .where(BacktraceHop.chain_id.in_([c.id for c in chains]))
            .group_by(BacktraceHop.chain_id)
        )
    ).all()
    counts = dict(count_rows)

    return [
        BacktraceChainSummaryOut(
            id=c.id,
            origin_paper_id=c.origin_paper_id,
            origin_text=c.origin_text,
            hop_count=counts.get(c.id, 0),
            created_at=c.created_at,
        )
        for c in chains
    ]
