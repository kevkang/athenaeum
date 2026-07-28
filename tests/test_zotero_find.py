from athenaeum.ingest.zotero import ZoteroSearchHit
from athenaeum.provider.base import Message
from athenaeum.synthesize.zotero_find import extract_search_terms, rank_candidates


def _hit(item_key: str, abstract: str | None) -> ZoteroSearchHit:
    return ZoteroSearchHit(
        item_key=item_key,
        title=f"Paper {item_key}",
        creators=["A. Author"],
        year=2020,
        venue="Journal of Tests",
        doi=None,
        item_type="journalArticle",
        abstract=abstract,
    )


# ── extract_search_terms ────────────────────────────────────────────────────────

def test_extract_search_terms_filters_stopwords_and_short_words() -> None:
    terms = extract_search_terms("what is the effect of melatonin on the aging brain")
    assert "melatonin" in terms
    assert "aging" in terms
    assert "brain" in terms
    assert "what" not in terms
    assert "the" not in terms


def test_extract_search_terms_respects_max_terms() -> None:
    terms = extract_search_terms("alpha beta gamma delta epsilon zeta", max_terms=3)
    assert len(terms) == 3


def test_extract_search_terms_falls_back_to_raw_query_when_all_stopwords() -> None:
    terms = extract_search_terms("of the a an")
    assert terms == ["of the a an"]


def test_extract_search_terms_empty_query() -> None:
    assert extract_search_terms("   ") == []


# ── rank_candidates ──────────────────────────────────────────────────────────────

class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        return None, self._tool_result


async def test_rank_candidates_excludes_hits_with_no_abstract() -> None:
    hits = [_hit("A", None), _hit("B", "Melatonin reduces oxidative stress in mitochondria.")]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {
                "candidate_index": 0,
                "quote": "Melatonin reduces oxidative stress in mitochondria.",
                "relevance_note": "on topic",
            },
        ]}}
    )

    matches = await rank_candidates(provider, "melatonin oxidative stress", hits)

    assert len(matches) == 1
    assert matches[0].hit.item_key == "B"
    # Only the abstract-bearing candidate should even be shown to the LLM.
    assert "Paper A" not in provider.last_messages[0].content


async def test_rank_candidates_discards_fabricated_quote() -> None:
    hits = [_hit("A", "The real abstract text.")]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {
                "candidate_index": 0,
                "quote": "a quote that does not appear anywhere",
                "relevance_note": "x",
            },
        ]}}
    )

    matches = await rank_candidates(provider, "query", hits)

    assert matches == []


async def test_rank_candidates_discards_out_of_range_index() -> None:
    hits = [_hit("A", "Some abstract.")]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 5, "quote": "Some abstract.", "relevance_note": "x"},
        ]}}
    )

    matches = await rank_candidates(provider, "query", hits)

    assert matches == []


async def test_rank_candidates_returns_empty_when_no_quotable_hits() -> None:
    hits = [_hit("A", None), _hit("B", "")]
    provider = _FakeProvider({"name": "select_relevant_items", "input": {"results": []}})

    matches = await rank_candidates(provider, "query", hits)

    assert matches == []
    # The provider should never even be called — nothing to quote from.
    assert provider.last_messages is None


async def test_rank_candidates_handles_no_tool_result() -> None:
    hits = [_hit("A", "Some abstract.")]
    provider = _FakeProvider(None)

    matches = await rank_candidates(provider, "query", hits)

    assert matches == []
