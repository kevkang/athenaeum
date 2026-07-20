from athenaeum.provider.base import Message
from athenaeum.synthesize.find_ranking import FindCandidate, expand_search_queries, rank_candidates


def _candidate(source: str, abstract: str | None) -> FindCandidate:
    return FindCandidate(
        title=f"Paper {source}",
        creators=["A. Author"],
        year=2020,
        venue="Journal of Tests",
        doi=None,
        abstract=abstract,
        source=source,
    )


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        return None, self._tool_result


async def test_rank_candidates_excludes_hits_with_no_abstract() -> None:
    candidates = [
        _candidate("A", None),
        _candidate("B", "Melatonin reduces oxidative stress in mitochondria."),
    ]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 0, "quote": "Melatonin reduces oxidative stress in mitochondria.", "relevance_note": "on topic"},
        ]}}
    )

    matches = await rank_candidates(provider, "melatonin oxidative stress", candidates)

    assert len(matches) == 1
    assert matches[0].candidate.source == "B"
    assert "Paper A" not in provider.last_messages[0].content


async def test_rank_candidates_discards_fabricated_quote() -> None:
    candidates = [_candidate("A", "The real abstract text.")]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 0, "quote": "a quote that does not appear anywhere", "relevance_note": "x"},
        ]}}
    )

    matches = await rank_candidates(provider, "query", candidates)

    assert matches == []


async def test_rank_candidates_returns_empty_when_no_quotable_candidates() -> None:
    candidates = [_candidate("A", None), _candidate("B", "")]
    provider = _FakeProvider({"name": "select_relevant_items", "input": {"results": []}})

    matches = await rank_candidates(provider, "query", candidates)

    assert matches == []
    assert provider.last_messages is None


# ── expand_search_queries ───────────────────────────────────────────────────────

async def test_expand_search_queries_returns_llm_variants() -> None:
    provider = _FakeProvider(
        {"name": "interpret_query", "input": {
            "entities": ["melatonin"],
            "relations": ["reduces"],
            "expanded_terms": ["oxidative stress"],
            "search_queries": ["melatonin oxidative stress", "melatonin mitochondria aging"],
        }}
    )

    queries = await expand_search_queries(provider, "does melatonin reduce oxidative stress")

    assert queries == ["melatonin oxidative stress", "melatonin mitochondria aging"]


async def test_expand_search_queries_respects_max_queries() -> None:
    provider = _FakeProvider(
        {"name": "interpret_query", "input": {
            "entities": [], "relations": [], "expanded_terms": [],
            "search_queries": ["a", "b", "c", "d", "e"],
        }}
    )

    queries = await expand_search_queries(provider, "query", max_queries=2)

    assert queries == ["a", "b"]


async def test_expand_search_queries_falls_back_when_no_tool_result() -> None:
    provider = _FakeProvider(None)

    queries = await expand_search_queries(provider, "original query")

    assert queries == ["original query"]


async def test_expand_search_queries_falls_back_when_empty_search_queries() -> None:
    provider = _FakeProvider(
        {"name": "interpret_query", "input": {
            "entities": [], "relations": [], "expanded_terms": [], "search_queries": [],
        }}
    )

    queries = await expand_search_queries(provider, "original query")

    assert queries == ["original query"]
