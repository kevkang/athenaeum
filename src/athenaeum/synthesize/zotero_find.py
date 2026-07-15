""""Find" over a user's whole Zotero library — a lightweight, synchronous
sibling to the main synthesis orchestrator.

Unlike run_synthesis (5-step, async job, WebSocket trace, persisted
Synthesis/evidence rows tied to already-ingested PaperSentence FKs), this is
a single forced-tool-call LLM request over Zotero metadata/abstracts, since
most candidates here haven't been imported/ingested and so have no full text
to run the heavier pipeline against.

The actual ranking call is corpus-agnostic (see find_ranking.py, shared with
openalex_find.py) — this module just maps ZoteroSearchHit into that shape and
back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from athenaeum.ingest.zotero import ZoteroSearchHit
from athenaeum.provider.base import Provider
from athenaeum.synthesize.find_ranking import FindCandidate
from athenaeum.synthesize.find_ranking import rank_candidates as _rank_candidates

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


def _to_candidate(hit: ZoteroSearchHit) -> FindCandidate:
    return FindCandidate(
        title=hit.title,
        creators=hit.creators,
        year=hit.year,
        venue=hit.venue,
        doi=hit.doi,
        abstract=hit.abstract,
        source=hit,
    )


async def rank_candidates(
    provider: Provider, query: str, hits: list[ZoteroSearchHit]
) -> list[ZoteroFindMatch]:
    matches = await _rank_candidates(provider, query, [_to_candidate(h) for h in hits])
    return [
        ZoteroFindMatch(hit=m.candidate.source, quote=m.quote, relevance_note=m.relevance_note)
        for m in matches
    ]
