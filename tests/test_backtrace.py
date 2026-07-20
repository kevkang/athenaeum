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

async def test_identify_cited_work_no_candidates_returns_unconfident() -> None:
    provider = _FakeProvider(None)

    result = await backtrace.identify_cited_work(provider, "some sentence", [])

    assert result.candidate is None
    assert result.confident is False
    assert provider.last_messages is None  # never called the LLM


async def test_identify_cited_work_confident_match() -> None:
    candidates = [
        backtrace.ReferenceCandidate(openalex_id="W1", title="Paper A", authors=["Smith"], year=2020),
        backtrace.ReferenceCandidate(openalex_id="W2", title="Paper B", authors=["Jones"], year=2018),
    ]
    provider = _FakeProvider(
        {"name": "identify_cited_work", "input": {
            "candidate_index": 0, "reasoning": "Author/year match Smith 2020.", "confident": True,
        }}
    )

    result = await backtrace.identify_cited_work(provider, "consistent with Smith et al. 2020", candidates)

    assert result.candidate is not None
    assert result.candidate.openalex_id == "W1"
    assert result.confident is True


async def test_identify_cited_work_low_confidence_still_surfaces_guess() -> None:
    """confident=False doesn't null the candidate — callers (e.g. api/backtrace.py's
    _resolve_hop) use `confident` alone to decide whether to auto-proceed; the guess
    itself is preserved for the UI/manual-pick fallback."""
    candidates = [backtrace.ReferenceCandidate(openalex_id="W1", title="Paper A", authors=["Smith"], year=2020)]
    provider = _FakeProvider(
        {"name": "identify_cited_work", "input": {
            "candidate_index": 0, "reasoning": "Weak signal.", "confident": False,
        }}
    )

    result = await backtrace.identify_cited_work(provider, "some vague sentence", candidates)

    assert result.candidate is not None
    assert result.candidate.openalex_id == "W1"
    assert result.confident is False


async def test_identify_cited_work_out_of_range_index_ignored() -> None:
    candidates = [backtrace.ReferenceCandidate(openalex_id="W1", title="Paper A", authors=["Smith"], year=2020)]
    provider = _FakeProvider(
        {"name": "identify_cited_work", "input": {
            "candidate_index": 5, "reasoning": "hallucinated index", "confident": True,
        }}
    )

    result = await backtrace.identify_cited_work(provider, "sentence", candidates)

    assert result.candidate is None


async def test_identify_cited_work_no_tool_result() -> None:
    candidates = [backtrace.ReferenceCandidate(openalex_id="W1", title="Paper A", authors=["Smith"], year=2020)]
    provider = _FakeProvider(None)

    result = await backtrace.identify_cited_work(provider, "sentence", candidates)

    assert result.candidate is None
    assert result.confident is False


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
