"""Backtrace: walk a citation chain one hop at a time.

Given a claim and the paper that states it, identify which of that paper's
own OpenAlex references most plausibly supports the same claim (an LLM best
guess — there's no ground truth linking a specific sentence to a specific
reference, see BACKTRACE_IDENTIFY_SYSTEM), locate a verbatim quote for the
claim in the identified paper (abstract first; full text is a separate,
heavier step orchestrated by api/backtrace.py + worker/jobs.py when the
abstract doesn't have it), and repeat from there.

See api/backtrace.py for the request/response orchestration (creating
BacktraceHop rows, enqueuing ingestion) and worker/jobs.py for the
full-text-ingestion tail that calls back into search_full_text_for_claim.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.source_check import MATCHES_SOURCE
from athenaeum.ingest.embed import embed_sentences
from athenaeum.ingest.openalex import (
    OpenAlexMeta,
    fetch_referenced_work_ids,
    fetch_works_by_ids,
)
from athenaeum.provider.base import Message, Provider
from athenaeum.retrieve.hybrid import search as hybrid_search
from athenaeum.storage.models import PaperSentence, Passage
from athenaeum.synthesize.find_ranking import FindCandidate, rank_candidates
from athenaeum.synthesize.passage_util import is_contiguous
from athenaeum.synthesize.prompts import (
    BACKTRACE_IDENTIFY_SYSTEM,
    EXTRACT_PASSAGES_TOOL,
    EXTRACT_SYSTEM,
    RANK_REFERENCES_TOOL,
)

log = structlog.get_logger()


@dataclass
class ReferenceCandidate:
    openalex_id: str
    title: str
    authors: list[str]
    year: int | None
    doi: str | None = None
    venue: str | None = None


@dataclass
class RankedCandidate:
    candidate: ReferenceCandidate
    reasoning: str
    confident: bool


@dataclass
class IdentifyResult:
    candidates: list[RankedCandidate]


def _names(meta_authors: list[dict]) -> list[str]:
    return [a["name"] for a in meta_authors if a.get("name")]


async def resolve_reference_candidates(from_openalex_id: str) -> list[ReferenceCandidate]:
    """Bulk-fetch title/author/year for every work `from_openalex_id` cites."""
    loop = asyncio.get_running_loop()
    ref_ids = await loop.run_in_executor(None, fetch_referenced_work_ids, from_openalex_id)
    if not ref_ids:
        return []
    metas = await loop.run_in_executor(None, fetch_works_by_ids, ref_ids)
    return [
        ReferenceCandidate(
            openalex_id=m.openalex_id,
            title=m.title,
            authors=_names(m.authors),
            year=m.year,
            doi=m.doi,
            venue=m.venue,
        )
        for m in metas
        if m.openalex_id
    ]


def _format_candidates(candidates: list[ReferenceCandidate]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        authors = ", ".join(c.authors) or "unknown authors"
        lines.append(f"[{i}] {c.title} ({c.year or '?'}) — {authors}")
    return "\n".join(lines)


async def identify_cited_work(
    provider: Provider, citing_text: str, candidates: list[ReferenceCandidate]
) -> IdentifyResult:
    """Rank which of `candidates` the citing sentence could plausibly invoke —
    a human picks one (or several, to open as parallel branches) from the
    result, so this never auto-commits to a single guess itself."""
    if not candidates:
        return IdentifyResult(candidates=[])

    user_msg = (
        f"CITING SENTENCE:\n{citing_text}\n\n"
        f"CANDIDATE REFERENCES:\n{_format_candidates(candidates)}"
    )
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=user_msg)],
        system=BACKTRACE_IDENTIFY_SYSTEM,
        tools=[RANK_REFERENCES_TOOL],
    )
    if tool_result is None:
        return IdentifyResult(candidates=[])

    ranked: list[RankedCandidate] = []
    for m in tool_result["input"].get("matches", []):
        idx = m.get("candidate_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
            continue
        ranked.append(
            RankedCandidate(
                candidate=candidates[idx],
                reasoning=m.get("reasoning", ""),
                confident=bool(m.get("confident", False)),
            )
        )
    # Confident matches first; stable sort preserves the model's own
    # most-to-least-plausible ordering within each confidence bucket, so
    # candidates[0] is the sensible default to pre-check in the UI.
    ranked.sort(key=lambda r: not r.confident)
    return IdentifyResult(candidates=ranked)


async def extract_abstract_quote(
    provider: Provider, claim: str, meta: OpenAlexMeta,
) -> tuple[str, str] | None:
    """Try to find a verbatim quote supporting `claim` in `meta`'s own abstract.
    Returns (quote, relevance_note), or None if no verified match."""
    if not meta.abstract:
        return None
    candidate = FindCandidate(
        title=meta.title,
        creators=_names(meta.authors),
        year=meta.year,
        venue=None,
        doi=meta.doi,
        abstract=meta.abstract,
        source=meta,
    )
    matches = await rank_candidates(provider, claim, [candidate])
    if not matches:
        return None
    m = matches[0]
    return m.quote, m.relevance_note


async def search_full_text_for_claim(
    provider: Provider,
    session: AsyncSession,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    claim: str,
    embed_model: str,
) -> Passage | None:
    """Search within one specific paper's own sentences for the best passage
    supporting `claim`, reusing the same embed -> hybrid-search -> extract
    shape as the main synthesis pipeline (orchestrator._step_retrieval /
    _step_extract), scoped to a single paper via hybrid.search's paper_id
    param. Persists a real Passage (with rationale) on a match, same as the
    main pipeline, so it can be promoted/reused like any other evidence."""
    embedding = (await embed_sentences([claim], embed_model))[0]
    hits = await hybrid_search(
        session=session,
        project_id=project_id,
        query_text=claim,
        query_embedding=embedding,
        top_k=12,
        mode="hybrid",
        paper_id=paper_id,
    )
    if not hits:
        return None

    hits_text = "\n".join(f"[{h.sentence_id}] {h.text}" for h in hits)
    user_msg = (
        f"CLAIM: {claim}\n\nRETRIEVED SENTENCES ({len(hits)} total):\n{hits_text}\n\n"
        "Select the single best group of sentences that provides evidence for the claim, if any."
    )
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=user_msg)],
        system=EXTRACT_SYSTEM,
        tools=[EXTRACT_PASSAGES_TOOL],
    )
    if tool_result is None:
        return None

    passages_in = tool_result["input"].get("passages", [])
    if not passages_in:
        return None
    pd = passages_in[0]

    valid_ids = {str(h.sentence_id) for h in hits}
    sids = [s for s in pd.get("sentence_ids", []) if s in valid_ids]
    if not sids:
        return None
    sentence_uuids = [uuid.UUID(s) for s in sids]

    rows = (
        await session.scalars(
            select(PaperSentence).where(PaperSentence.id.in_(sentence_uuids)).order_by(PaperSentence.ord)
        )
    ).all()
    if not is_contiguous([r.ord for r in rows]):
        # See orchestrator.py's identical check: a non-contiguous selection
        # would still join into what reads as one continuous quote, silently
        # skipping over whatever sentence(s) sit in the gap.
        log.warning(
            "backtrace.non_contiguous_sentences",
            sentence_ids=sids,
            ords=[r.ord for r in rows],
        )
        return None
    verbatim = " ".join(r.text for r in rows)
    if not verbatim:
        return None

    passage = Passage(
        id=uuid.uuid4(),
        paper_id=paper_id,
        sentence_ids=sentence_uuids,
        verbatim_text=verbatim,
        # The words were just copied from this server's sentences, so they match.
        source_check=MATCHES_SOURCE,
        rationale=pd.get("rationale") or None,
    )
    session.add(passage)
    await session.flush()
    return passage
