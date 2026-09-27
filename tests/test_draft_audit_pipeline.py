"""Tests for pulling citations out of a draft and matching them to works.

No network and no model: `search_works` is monkeypatched and the provider is a
stub, so these pin the deterministic rules rather than anything a model decided.
That is the point — the rules exist precisely so that a model's ranking never
reaches an audit on its own.
"""
from __future__ import annotations

import pytest

from athenaeum.domain.draft_audit import EXACT, NEEDS_CONFIRMATION, NOT_FOUND
from athenaeum.ingest.openalex import OpenAlexSearchHit
from athenaeum.provider.base import Message
from athenaeum.synthesize import draft_audit
from athenaeum.synthesize.draft_audit import (
    ExtractedCitation,
    extract_citations,
    find_doi,
    normalize_title,
    pick_exact_match,
    resolve_citation,
)

_TITLE = "Estimating taxon-specific growth rates in soil by stable isotope probing"


def _hit(
    title: str = _TITLE, doi: str | None = "10.1038/s41586-018-0001", **kw
) -> OpenAlexSearchHit:
    return OpenAlexSearchHit(
        openalex_id=kw.get("openalex_id", "https://openalex.org/W1"),
        doi=doi,
        title=title,
        first_author=kw.get("first_author", "A. Koch"),
        venue=kw.get("venue", "Nature"),
        year=kw.get("year", 2018),
        abstract=kw.get("abstract", "Growth rates spanned two orders of magnitude."),
        cited_by_count=kw.get("cited_by_count", 12),
    )


# ── DOI detection ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Koch et al. 2018. https://doi.org/10.1038/s41586-018-0001", "10.1038/s41586-018-0001"),
        ("See doi:10.1234/ABC.5678 for detail", "10.1234/abc.5678"),
        # A DOI ending a sentence must not swallow the full stop.
        ("...as reported (10.1038/nature12373).", "10.1038/nature12373"),
        ("(Koch et al. 2018)", None),
        ("", None),
    ],
)
def test_find_doi(text: str, expected: str | None) -> None:
    assert find_doi(text) == expected


# ── Title normalisation ──────────────────────────────────────────────────────

def test_normalize_title_folds_what_varies_between_spellings() -> None:
    """Case, accents, punctuation and whitespace differ between a reference
    list and a metadata record without meaning anything different."""
    a = normalize_title("Sour­ce Sepa­ration: A Réview, 2nd ed.")
    b = normalize_title("SOURCE   SEPARATION   a  review  2nd  ed")

    assert normalize_title("Soil  Carbon!") == "soil carbon"
    assert a.replace("­", "") == a  # no stray control characters survive
    assert b == "source separation a review 2nd ed"


# ── Exact matching ───────────────────────────────────────────────────────────

def test_exact_match_when_one_title_appears_verbatim() -> None:
    citation = f"Koch, A. et al. (2018). {_TITLE}. Nature 555, 1-9."

    hits = [_hit(), _hit(title="Something else entirely here")]

    assert pick_exact_match(citation, hits) is not None


def test_no_exact_match_when_two_titles_both_appear() -> None:
    """Ambiguity is not resolved by picking the first. Two plausible works is
    a question for a person, not a coin flip."""
    # A citation naming the paper and its follow-up. Both titles are verbatim
    # substrings of it, so neither can be called the match.
    citation = f"{_TITLE}. See also {_TITLE} revisited."
    hits = [_hit(), _hit(title=f"{_TITLE} revisited")]

    assert pick_exact_match(citation, hits) is None


def test_short_titles_never_match_exactly() -> None:
    """'Introduction' appearing in a citation is not evidence of anything."""
    assert pick_exact_match("Smith 2020, Introduction, p. 4", [_hit(title="Introduction")]) is None


def test_no_match_when_the_title_is_absent() -> None:
    assert pick_exact_match("(Koch et al. 2018)", [_hit()]) is None


# ── Resolution ───────────────────────────────────────────────────────────────

def _patch_search(monkeypatch, hits: list[OpenAlexSearchHit]) -> None:
    monkeypatch.setattr(draft_audit, "search_works", lambda q, per_page, email: hits)


async def test_a_doi_in_the_draft_resolves_exactly(monkeypatch) -> None:
    _patch_search(monkeypatch, [_hit()])
    citation = ExtractedCitation(
        claim_text="Microbes grow fast.",
        citation_text="Koch et al. 2018. https://doi.org/10.1038/s41586-018-0001",
    )

    result = await resolve_citation(citation)

    assert result.resolution == EXACT
    assert result.match is not None and result.match.doi == "10.1038/s41586-018-0001"


async def test_a_doi_the_search_does_not_return_is_not_an_exact_match(monkeypatch) -> None:
    """The author named a work outright, but the search came back with
    something else. Trusting it would audit the wrong paper."""
    _patch_search(monkeypatch, [_hit(doi="10.9999/different", title="A different paper entirely")])
    citation = ExtractedCitation(
        claim_text="Microbes grow fast.",
        citation_text="Koch et al. 2018. doi:10.1038/s41586-018-0001",
    )

    result = await resolve_citation(citation)

    assert result.resolution == NEEDS_CONFIRMATION
    assert result.match is None


async def test_a_bare_author_year_citation_needs_confirmation(monkeypatch) -> None:
    """No DOI and no title to match on. Candidates are offered; nothing is
    audited until a person picks one."""
    _patch_search(monkeypatch, [_hit(), _hit(title="Another plausible soil paper", doi=None)])

    result = await resolve_citation(
        ExtractedCitation(claim_text="Microbes grow fast.", citation_text="(Koch et al. 2018)")
    )

    assert result.resolution == NEEDS_CONFIRMATION
    assert result.match is None
    assert len(result.candidates) == 2


async def test_no_candidates_is_not_found(monkeypatch) -> None:
    _patch_search(monkeypatch, [])

    result = await resolve_citation(
        ExtractedCitation(claim_text="Microbes grow fast.", citation_text="(Nobody 1899)")
    )

    assert result.resolution == NOT_FOUND
    assert result.match is None


async def test_a_failed_search_is_not_found_rather_than_an_error(monkeypatch) -> None:
    """One unreachable lookup must not fail the whole audit run."""
    def _boom(q, per_page, email):
        raise RuntimeError("openalex unreachable")

    monkeypatch.setattr(draft_audit, "search_works", _boom)

    result = await resolve_citation(
        ExtractedCitation(claim_text="Microbes grow fast.", citation_text="(Koch et al. 2018)")
    )

    assert result.resolution == NOT_FOUND


# ── Extraction ───────────────────────────────────────────────────────────────

class _StubProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_system: str | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_system = system
        self.last_messages: list[Message] = messages
        return None, self._tool_result


async def test_extraction_returns_claim_citation_pairs() -> None:
    provider = _StubProvider({
        "name": "extract_citations",
        "input": {
            "citations": [
                {"claim_text": "Microbes grow fast.", "citation_text": "Koch et al. 2018. Nature."},
                {"claim_text": "Microbes grow fast.", "citation_text": "Miller 1959."},
            ]
        },
    })

    result = await extract_citations(provider, "Microbes grow fast (Koch 2018; Miller 1959).")

    assert [c.citation_text for c in result] == ["Koch et al. 2018. Nature.", "Miller 1959."]
    # One sentence citing two works produces two entries sharing the claim.
    assert {c.claim_text for c in result} == {"Microbes grow fast."}


async def test_extraction_drops_entries_missing_either_half() -> None:
    """A claim with no citation cannot be checked; a citation with no claim has
    nothing to check against. Neither is an error, both are dropped."""
    provider = _StubProvider({
        "name": "extract_citations",
        "input": {
            "citations": [
                {"claim_text": "Microbes grow fast.", "citation_text": ""},
                {"claim_text": "", "citation_text": "Koch et al. 2018."},
                {"claim_text": "Soil warms.", "citation_text": "Smith 2020."},
                "not even a dict",
            ]
        },
    })

    result = await extract_citations(provider, "some draft")

    assert [c.claim_text for c in result] == ["Soil warms."]


@pytest.mark.parametrize("tool_result", [None, {"name": "x", "input": {"citations": "nope"}}])
async def test_unusable_extraction_response_yields_no_citations(tool_result) -> None:
    assert await extract_citations(_StubProvider(tool_result), "some draft") == []


async def test_empty_draft_never_calls_the_model() -> None:
    provider = _StubProvider({"name": "x", "input": {"citations": [{"claim_text": "a",
                                                                    "citation_text": "b"}]}})

    assert await extract_citations(provider, "   \n  ") == []
    assert provider.last_system is None
