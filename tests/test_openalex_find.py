from athenaeum.ingest.openalex import OpenAlexSearchHit
from athenaeum.synthesize.openalex_find import rank_candidates


def _hit(openalex_id: str, first_author: str | None, abstract: str | None) -> OpenAlexSearchHit:
    return OpenAlexSearchHit(
        openalex_id=openalex_id,
        doi=f"10.1234/{openalex_id}",
        title=f"Paper {openalex_id}",
        first_author=first_author,
        venue="Journal of Tests",
        year=2020,
        abstract=abstract,
        cited_by_count=5,
    )


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        return None, self._tool_result


async def test_rank_candidates_maps_singular_first_author_to_creators_list() -> None:
    hits = [_hit("W1", "Ada Borton", "Methane fluxes correlate with temperature.")]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {
                "candidate_index": 0,
                "quote": "Methane fluxes correlate with temperature.",
                "relevance_note": "on topic",
            },
        ]}}
    )

    matches = await rank_candidates(provider, "methane temperature", hits)

    assert len(matches) == 1
    assert matches[0].hit.openalex_id == "W1"
    assert matches[0].quote == "Methane fluxes correlate with temperature."


async def test_rank_candidates_handles_no_author() -> None:
    hits = [_hit("W1", None, "Some abstract text.")]
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 0, "quote": "Some abstract text.", "relevance_note": "x"},
        ]}}
    )

    matches = await rank_candidates(provider, "query", hits)

    assert len(matches) == 1


async def test_rank_candidates_excludes_hits_with_no_abstract() -> None:
    hits = [_hit("W1", "Ada Borton", None)]
    provider = _FakeProvider({"name": "select_relevant_items", "input": {"results": []}})

    matches = await rank_candidates(provider, "query", hits)

    assert matches == []
