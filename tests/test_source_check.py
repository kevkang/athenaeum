"""Tests for checking a quote against this server's copy of a paper.

The comparison has to tolerate the ways two servers' PDF text extraction
innocently differs, and nothing else. So these tests come in two groups: the
harmless differences that must still match, and the meaningful changes (a
number, a word) that must never match.
"""
from __future__ import annotations

import uuid

from athenaeum.domain.source_check import (
    DIFFERS_FROM_SOURCE,
    MATCHES_SOURCE,
    SOURCE_UNAVAILABLE,
    check_quote,
    normalize,
)


def _sentences(*texts: str) -> list[tuple[uuid.UUID, str]]:
    return [(uuid.uuid4(), t) for t in texts]


# ── Harmless differences: must still match ───────────────────────────────────

def test_identical_sentence_matches_and_points_at_it() -> None:
    sents = _sentences("Intro sentence.", "Soil CO2 efflux rose 28% under warming.", "Outro.")

    result = check_quote("Soil CO2 efflux rose 28% under warming.", sents)

    assert result.state == MATCHES_SOURCE
    assert result.sentence_ids == (sents[1][0],)


def test_spacing_line_break_hyphen_and_subscript_differences_still_match() -> None:
    sents = _sentences("Soil CO₂ ef- flux  rose 28%\nunder warming.")

    result = check_quote("Soil CO2 efflux rose 28% under warming.", sents)

    assert result.state == MATCHES_SOURCE


def test_ligatures_fold_to_plain_letters() -> None:
    sents = _sentences("A signiﬁcant ﬂux was observed.")

    assert check_quote("A significant flux was observed.", sents).state == MATCHES_SOURCE


def test_quote_spanning_two_sentences_points_at_both() -> None:
    sents = _sentences("First part of the finding.", "Second part of it.", "Unrelated.")

    result = check_quote("First part of the finding. Second part of it.", sents)

    assert result.state == MATCHES_SOURCE
    assert result.sentence_ids == (sents[0][0], sents[1][0])


def test_different_sentence_splitting_between_servers_still_matches() -> None:
    """The exporting server may have split one sentence where this one didn't
    (or vice versa); a quote that starts or ends mid-sentence here is fine."""
    sents = _sentences("Background. Warming raised efflux by 28% in plot A, but not in B.")

    result = check_quote("Warming raised efflux by 28% in plot A", sents)

    assert result.state == MATCHES_SOURCE
    assert result.sentence_ids == (sents[0][0],)


# ── Meaningful changes: must never match ─────────────────────────────────────

def test_a_changed_number_differs() -> None:
    sents = _sentences("Soil CO2 efflux rose 28% under warming.")

    result = check_quote("Soil CO2 efflux rose 82% under warming.", sents)

    assert result.state == DIFFERS_FROM_SOURCE


def test_a_changed_word_differs() -> None:
    sents = _sentences("Warming increased soil respiration.")

    assert check_quote("Warming decreased soil respiration.", sents).state == DIFFERS_FROM_SOURCE


def test_quote_not_in_the_paper_differs_and_points_at_nothing() -> None:
    result = check_quote("A sentence from some other paper.", _sentences("This paper's only text."))

    assert result.state == DIFFERS_FROM_SOURCE
    assert result.sentence_ids == ()


def test_empty_quote_cannot_be_confirmed() -> None:
    assert check_quote("   ", _sentences("Some text.")).state == DIFFERS_FROM_SOURCE


# ── No text for the paper on this server ─────────────────────────────────────

def test_paper_without_sentences_is_unavailable_not_differing() -> None:
    """Not having the paper is not evidence against the quote."""
    result = check_quote("Anything at all.", [])

    assert result.state == SOURCE_UNAVAILABLE
    assert result.sentence_ids == ()


def test_normalize_keeps_letters_digits_and_punctuation() -> None:
    assert normalize("  Rose 28%,\tnot 82%. ") == "Rose28%,not82%."
