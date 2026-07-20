"""Corpus-agnostic LLM ranking core shared by every "Find" search (Zotero
sync/local, OpenAlex, and any future corpus): given a query and a list of
title/author/year/abstract candidates, select the relevant ones and return a
verbatim quote from each one's own abstract plus a one-line relevance note.

See synthesize/zotero_find.py and synthesize/openalex_find.py for the thin,
corpus-specific wrappers that map their own search-hit dataclass into
FindCandidate before calling rank_candidates() here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from athenaeum.provider.base import Message, Provider
from athenaeum.synthesize.prompts import (
    INTERPRET_QUERY_TOOL,
    QUERY_INTERP_SYSTEM,
    SELECT_RELEVANT_ITEMS_TOOL,
    ZOTERO_FIND_SYSTEM,
)


@dataclass
class FindCandidate:
    title: str
    creators: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    abstract: str | None
    source: Any  # the original corpus-specific hit (ZoteroSearchHit, OpenAlexSearchHit, ...)


@dataclass
class FindMatch:
    candidate: FindCandidate
    quote: str
    relevance_note: str


def _format_candidates_for_prompt(candidates: list[FindCandidate]) -> str:
    lines: list[str] = []
    for i, c in enumerate(candidates):
        authors = ", ".join(c.creators) or "unknown authors"
        lines.append(f"[{i}] {c.title} ({c.year or '?'}) — {authors}\nAbstract: {c.abstract}")
    return "\n\n".join(lines)


async def rank_candidates(
    provider: Provider, query: str, candidates: list[FindCandidate]
) -> list[FindMatch]:
    """Rank/select relevant candidates via one forced-tool-call LLM request.

    Candidates with no abstract are excluded before the call (nothing to
    quote). Any result whose quote isn't an actual verbatim substring of that
    candidate's abstract is discarded — defense in depth beyond the prompt's
    "never fabricate" instruction.
    """
    quotable = [c for c in candidates if c.abstract]
    if not quotable:
        return []

    candidates_text = _format_candidates_for_prompt(quotable)
    user_msg = f"QUERY: {query}\n\nCANDIDATES:\n{candidates_text}"

    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=user_msg)],
        system=ZOTERO_FIND_SYSTEM,
        tools=[SELECT_RELEVANT_ITEMS_TOOL],
    )
    if tool_result is None:
        return []

    matches: list[FindMatch] = []
    for result in tool_result["input"].get("results", []):
        idx = result.get("candidate_index")
        quote = result.get("quote", "")
        note = result.get("relevance_note", "")
        if not isinstance(idx, int) or idx < 0 or idx >= len(quotable):
            continue
        candidate = quotable[idx]
        if not quote or quote not in (candidate.abstract or ""):
            continue
        matches.append(FindMatch(candidate=candidate, quote=quote, relevance_note=note))
    return matches


async def expand_search_queries(provider: Provider, query: str, max_queries: int = 4) -> list[str]:
    """Ask the LLM for several distinct search phrasings covering `query` from
    different angles — reuses the same query-interpretation step the main
    synthesis pipeline uses (orchestrator._step_query_interp). A single
    natural-language query often under-matches an external search API's own
    (keyword/relevance-based, not semantic) ranking; running a few targeted
    variants and merging results gives the downstream ranking step a broader,
    better pool to choose from. Falls back to [query] if the call fails to
    produce anything usable.
    """
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=f"Parse this research claim:\n\n{query}")],
        system=QUERY_INTERP_SYSTEM,
        tools=[INTERPRET_QUERY_TOOL],
    )
    if tool_result is None:
        return [query]
    queries = tool_result["input"].get("search_queries") or []
    return queries[:max_queries] if queries else [query]
