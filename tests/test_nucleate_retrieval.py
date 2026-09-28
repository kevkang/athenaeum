"""Retrieval for the Nucleate archivist: failures are counted, not hidden."""
from __future__ import annotations

import pyalex

from athenaeum.ingest.openalex import OpenAlexSearchHit
from athenaeum.nucleate import retrieval


def _hit(title: str) -> OpenAlexSearchHit:
    return OpenAlexSearchHit(openalex_id=f"https://openalex.org/W{abs(hash(title)) % 10**8}",
                             doi=None, title=title, first_author=None, venue=None, year=2020,
                             abstract=None, cited_by_count=0)


async def test_an_openalex_failure_ends_the_remaining_queries_and_is_counted(monkeypatch) -> None:
    def fake_search(query, per_page, email):
        if query == "rate limited":
            raise RuntimeError("too many 429 error responses")
        return [_hit(query)]

    monkeypatch.setattr(retrieval, "search_works", fake_search)

    result = await retrieval.retrieve_openalex(["works", "rate limited", "also skipped"])

    assert [c.title for c in result.candidates] == ["works"]
    # The failure and the query it made the run skip both count as failed.
    assert result.failed_queries == 2


def test_openalex_client_retries_rate_limits() -> None:
    """pyalex defaults to zero retries, so one 429 used to fail a query."""
    assert pyalex.config.max_retries >= 1
    assert 429 in pyalex.config.retry_http_codes


async def test_web_pages_become_candidates_only_when_they_match_a_work(monkeypatch) -> None:
    from athenaeum.synthesize.websearch_find import WebPage

    async def fake_pages(provider, query):
        return [WebPage("https://doi.org/10.5555/xyz", "Some page"),
                WebPage("https://blog.example/post", "A blog post")]

    def fake_search(query, per_page, email):
        if query == "10.5555/xyz":
            hit = _hit("The matched paper")
            hit.doi = "10.5555/xyz"
            return [hit]
        return []

    monkeypatch.setattr(retrieval, "search_web_pages", fake_pages)
    monkeypatch.setattr(retrieval, "search_works", fake_search)

    result = await retrieval.retrieve_web(provider=None, query="q")  # type: ignore[arg-type]

    assert [c.title for c in result.candidates] == ["The matched paper"]
    assert result.unmatched == 1


async def test_web_matching_stops_when_openalex_fails(monkeypatch) -> None:
    from athenaeum.synthesize.websearch_find import WebPage

    async def fake_pages(provider, query):
        return [WebPage(f"https://doi.org/10.5555/p{i}", f"Page {i}") for i in range(4)]

    calls = []

    def failing_search(query, per_page, email):
        calls.append(query)
        raise RuntimeError("too many 429 error responses")

    monkeypatch.setattr(retrieval, "search_web_pages", fake_pages)
    monkeypatch.setattr(retrieval, "search_works", failing_search)

    result = await retrieval.retrieve_web(provider=None, query="q")  # type: ignore[arg-type]

    assert len(calls) == 1
    assert result.candidates == []
    # Unchecked, not unmatched: nothing was compared, so nothing failed to match.
    assert (result.unmatched, result.unchecked) == (0, 4)
