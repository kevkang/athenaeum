""""Find" over a user's whole Zotero library — a lightweight, synchronous
sibling to the main synthesis orchestrator.

Unlike run_synthesis (5-step, async job, WebSocket trace, persisted
Synthesis/evidence rows tied to already-ingested PaperSentence FKs), this is
a single forced-tool-call LLM request over Zotero metadata/abstracts, since
most candidates here haven't been imported/ingested and so have no full text
to run the heavier pipeline against.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from athenaeum.ingest.zotero import ZoteroSearchHit
from athenaeum.provider.base import Message, Provider
from athenaeum.synthesize.prompts import SELECT_RELEVANT_ITEMS_TOOL, ZOTERO_FIND_SYSTEM

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "for", "to", "and", "or", "that", "which", "how",
    "what", "does", "do", "did", "this", "these", "those", "with", "from",
    "by", "as", "at", "can", "could", "would", "should", "it", "its", "into",
    "about", "between", "than", "then", "not", "no", "such", "any", "all",
}

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")


def extract_search_terms(query: str, max_terms: int = 4) -> list[str]:
    """Pure client-side keyword extraction — no LLM call.

    Zotero's quicksearch is a literal contains-match, not semantic — sending
    a full natural-language query as-is under-matches. This pulls out a few
    significant keywords to run as separate searches, merged by item_key by
    the caller, mirroring the multi-query merge pattern already used in
    orchestrator._step_retrieval.
    """
    seen: set[str] = set()
    keywords: list[str] = []
    for word in _WORD_RE.findall(query.lower()):
        if word in _STOPWORDS or len(word) < 3 or word in seen:
            continue
        seen.add(word)
        keywords.append(word)
    if not keywords:
        stripped = query.strip()
        return [stripped] if stripped else []
    return keywords[:max_terms]


@dataclass
class ZoteroFindMatch:
    hit: ZoteroSearchHit
    quote: str
    relevance_note: str


def _format_candidates_for_prompt(hits: list[ZoteroSearchHit]) -> str:
    lines: list[str] = []
    for i, hit in enumerate(hits):
        authors = ", ".join(hit.creators) or "unknown authors"
        lines.append(
            f"[{i}] {hit.title} ({hit.year or '?'}) — {authors}\nAbstract: {hit.abstract}"
        )
    return "\n\n".join(lines)


async def rank_candidates(
    provider: Provider, query: str, hits: list[ZoteroSearchHit]
) -> list[ZoteroFindMatch]:
    """Rank/select relevant candidates via one forced-tool-call LLM request.

    Candidates with no abstract are excluded before the call (nothing to
    quote). Any result whose quote isn't an actual verbatim substring of that
    candidate's abstract is discarded — defense in depth beyond the prompt's
    "never fabricate" instruction.
    """
    quotable = [h for h in hits if h.abstract]
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

    matches: list[ZoteroFindMatch] = []
    for result in tool_result["input"].get("results", []):
        idx = result.get("candidate_index")
        quote = result.get("quote", "")
        note = result.get("relevance_note", "")
        if not isinstance(idx, int) or idx < 0 or idx >= len(quotable):
            continue
        hit = quotable[idx]
        if not quote or quote not in (hit.abstract or ""):
            continue
        matches.append(ZoteroFindMatch(hit=hit, quote=quote, relevance_note=note))
    return matches
