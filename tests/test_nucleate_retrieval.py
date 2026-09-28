"""Retrieval for the Nucleate archivist: failures are counted, not hidden."""
from __future__ import annotations

import pyalex

from athenaeum.ingest.openalex import OpenAlexSearchHit
from athenaeum.nucleate import retrieval


def _hit(title: str) -> OpenAlexSearchHit:
    return OpenAlexSearchHit(openalex_id=f"https://openalex.org/W{abs(hash(title)) % 10**8}",
                             doi=None, title=title, first_author=None, venue=None, year=2020,
                             abstract=None, cited_by_count=0)


async def test_openalex_failures_are_counted_and_do_not_sink_other_queries(monkeypatch) -> None:
    def fake_search(query, per_page, email):
        if query == "rate limited":
            raise RuntimeError("too many 429 error responses")
        return [_hit(query)]

    monkeypatch.setattr(retrieval, "search_works", fake_search)

    result = await retrieval.retrieve_openalex(["works", "rate limited", "also works"])

    assert [c.title for c in result.candidates] == ["works", "also works"]
    assert result.failed_queries == 1


def test_openalex_client_retries_rate_limits() -> None:
    """pyalex defaults to zero retries, so one 429 used to fail a query."""
    assert pyalex.config.max_retries >= 3
    assert 429 in pyalex.config.retry_http_codes
