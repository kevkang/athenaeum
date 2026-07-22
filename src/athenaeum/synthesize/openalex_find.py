""""Find" over OpenAlex's global database — mirrors zotero_find.py's shape,
sharing the same corpus-agnostic ranking core (find_ranking.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from athenaeum.ingest.openalex import OpenAlexSearchHit
from athenaeum.provider.base import Provider
from athenaeum.synthesize.find_ranking import FindCandidate
from athenaeum.synthesize.find_ranking import rank_candidates as _rank_candidates


@dataclass
class OpenAlexFindMatch:
    hit: OpenAlexSearchHit
    quote: str
    relevance_note: str


def _to_candidate(hit: OpenAlexSearchHit) -> FindCandidate:
    return FindCandidate(
        title=hit.title,
        creators=[hit.first_author] if hit.first_author else [],
        year=hit.year,
        venue=hit.venue,
        doi=hit.doi,
        abstract=hit.abstract,
        source=hit,
    )


async def rank_candidates(
    provider: Provider, query: str, hits: list[OpenAlexSearchHit]
) -> list[OpenAlexFindMatch]:
    matches = await _rank_candidates(provider, query, [_to_candidate(h) for h in hits])
    return [
        OpenAlexFindMatch(hit=m.candidate.source, quote=m.quote, relevance_note=m.relevance_note)
        for m in matches
    ]
