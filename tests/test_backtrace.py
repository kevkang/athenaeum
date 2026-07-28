from athenaeum.ingest.openalex import OpenAlexMeta
from athenaeum.synthesize import backtrace


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        return None, self._tool_result


def _meta(openalex_id: str, abstract: str | None, doi: str | None = None) -> OpenAlexMeta:
    return OpenAlexMeta(
        openalex_id=openalex_id,
        doi=doi,
        title=f"Paper {openalex_id}",
        authors=[{"name": "Ada Borton", "orcid": None}],
        venue="Journal of Tests",
        year=2019,
        abstract=abstract,
        oa_pdf_url=None,
    )


# ── resolve_reference_candidates ────────────────────────────────────────────────

async def test_resolve_reference_candidates_maps_metadata(monkeypatch) -> None:
    monkeypatch.setattr(backtrace, "fetch_referenced_work_ids", lambda oid: ["W1", "W2"])
    monkeypatch.setattr(
        backtrace, "fetch_works_by_ids", lambda ids: [_meta("W1", "abs1"), _meta("W2", "abs2")]
    )

    candidates = await backtrace.resolve_reference_candidates("W0")

    assert [c.openalex_id for c in candidates] == ["W1", "W2"]
    assert candidates[0].authors == ["Ada Borton"]
    assert candidates[0].year == 2019


async def test_resolve_reference_candidates_empty_when_no_references(monkeypatch) -> None:
    monkeypatch.setattr(backtrace, "fetch_referenced_work_ids", lambda oid: [])

    candidates = await backtrace.resolve_reference_candidates("W0")

    assert candidates == []


# ── identify_cited_work ──────────────────────────────────────────────────────────
#
# identify_cited_work ranks *several* plausible candidates rather than
# auto-committing to one — a human picks (possibly several, to open as
# parallel branches) from the result. See api/backtrace.py's /rank endpoint.

async def test_identify_cited_work_no_candidates_returns_empty() -> None:
    provider = _FakeProvider(None)

    result = await backtrace.identify_cited_work(provider, "some sentence", [])

    assert result.candidates == []
    assert provider.last_messages is None  # never called the LLM


async def test_identify_cited_work_ranks_matches() -> None:
    candidates = [
        backtrace.ReferenceCandidate(
            openalex_id="W1", title="Paper A", authors=["Smith"], year=2020,
        ),
        backtrace.ReferenceCandidate(
            openalex_id="W2", title="Paper B", authors=["Jones"], year=2018,
        ),
    ]
    provider = _FakeProvider(
        {"name": "rank_cited_works", "input": {"matches": [
            {"candidate_index": 0, "reasoning": "Author/year match Smith 2020.", "confident": True},
        ]}}
    )

    result = await backtrace.identify_cited_work(
        provider, "consistent with Smith et al. 2020", candidates
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].candidate.openalex_id == "W1"
    assert result.candidates[0].confident is True


async def test_identify_cited_work_low_confidence_still_included() -> None:
    """confident=False entries aren't dropped — a human reviews every entry
    either way, so the UI needs to see them, not just the confident ones."""
    candidates = [
        backtrace.ReferenceCandidate(
            openalex_id="W1", title="Paper A", authors=["Smith"], year=2020,
        )
    ]
    provider = _FakeProvider(
        {"name": "rank_cited_works", "input": {"matches": [
            {"candidate_index": 0, "reasoning": "Weak signal.", "confident": False},
        ]}}
    )

    result = await backtrace.identify_cited_work(provider, "some vague sentence", candidates)

    assert len(result.candidates) == 1
    assert result.candidates[0].candidate.openalex_id == "W1"
    assert result.candidates[0].confident is False


async def test_identify_cited_work_out_of_range_index_skipped() -> None:
    """A hallucinated index is dropped from the ranking, not fatal to the
    whole response — the other, valid entries still come through."""
    candidates = [
        backtrace.ReferenceCandidate(
            openalex_id="W1", title="Paper A", authors=["Smith"], year=2020,
        )
    ]
    provider = _FakeProvider(
        {"name": "rank_cited_works", "input": {"matches": [
            {"candidate_index": 5, "reasoning": "hallucinated index", "confident": True},
            {"candidate_index": 0, "reasoning": "real match", "confident": True},
        ]}}
    )

    result = await backtrace.identify_cited_work(provider, "sentence", candidates)

    assert len(result.candidates) == 1
    assert result.candidates[0].candidate.openalex_id == "W1"


async def test_identify_cited_work_no_tool_result_returns_empty() -> None:
    candidates = [
        backtrace.ReferenceCandidate(
            openalex_id="W1", title="Paper A", authors=["Smith"], year=2020,
        )
    ]
    provider = _FakeProvider(None)

    result = await backtrace.identify_cited_work(provider, "sentence", candidates)

    assert result.candidates == []


async def test_identify_cited_work_sorts_confident_first() -> None:
    """The UI pre-checks candidates[0] as the one-click default (see
    BacktracePanel.tsx) — that only makes sense if confident matches sort
    ahead of unconfident ones regardless of the order the model emitted."""
    candidates = [
        backtrace.ReferenceCandidate(openalex_id="W1", title="A", authors=["Smith"], year=2020),
        backtrace.ReferenceCandidate(openalex_id="W2", title="B", authors=["Jones"], year=2018),
    ]
    provider = _FakeProvider(
        {"name": "rank_cited_works", "input": {"matches": [
            {"candidate_index": 0, "reasoning": "uncertain", "confident": False},
            {"candidate_index": 1, "reasoning": "clear match", "confident": True},
        ]}}
    )

    result = await backtrace.identify_cited_work(provider, "sentence", candidates)

    assert [r.candidate.openalex_id for r in result.candidates] == ["W2", "W1"]


# ── extract_abstract_quote ───────────────────────────────────────────────────────

async def test_extract_abstract_quote_no_abstract_returns_none() -> None:
    provider = _FakeProvider(None)
    meta = _meta("W1", None)

    result = await backtrace.extract_abstract_quote(provider, "claim", meta)

    assert result is None
    assert provider.last_messages is None


async def test_extract_abstract_quote_returns_verified_quote() -> None:
    meta = _meta("W1", "Methane flux increases sharply with soil temperature.")
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {
                "candidate_index": 0,
                "quote": "Methane flux increases sharply with soil temperature.",
                "relevance_note": "directly supports the claim",
            },
        ]}}
    )

    result = await backtrace.extract_abstract_quote(provider, "methane flux and temperature", meta)

    assert result is not None
    quote, note = result
    assert quote == "Methane flux increases sharply with soil temperature."
    assert note == "directly supports the claim"


async def test_extract_abstract_quote_discards_fabricated_quote() -> None:
    meta = _meta("W1", "The real abstract text.")
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 0, "quote": "a quote not in the abstract", "relevance_note": "x"},
        ]}}
    )

    result = await backtrace.extract_abstract_quote(provider, "claim", meta)

    assert result is None
