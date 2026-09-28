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

def test_hyphen_at_a_sentence_boundary_still_matches() -> None:
    """Regression: a source sentence split right after a hyphen ("... 15.1 +/-"
    then "14.3 pg/mL") joins into a hyphen and a space, exactly like the quote
    built from those sentences, so the two must normalize identically. Found
    in a real SciFact abstract."""
    sents = _sentences("Levels were higher (44.1 +/- 64.3 and 15.1 +/-", "14.3 pg/mL, P = 0.03).")
    quote = "Levels were higher (44.1 +/- 64.3 and 15.1 +/- 14.3 pg/mL, P = 0.03)."

    result = check_quote(quote, sents)

    assert result.state == MATCHES_SOURCE
    assert result.sentence_ids == (sents[0][0], sents[1][0])


# Characters that PDF extraction and typesetting vary, named by code point so
# no invisible or lookalike character sits in this file.
SOFT_HYPHEN = chr(0x00AD)
ZERO_WIDTH_SPACE = chr(0x200B)
ZERO_WIDTH_NON_JOINER = chr(0x200C)
ZERO_WIDTH_JOINER = chr(0x200D)
WORD_JOINER = chr(0x2060)
BYTE_ORDER_MARK = chr(0xFEFF)
NO_BREAK_SPACE = chr(0x00A0)
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
UNICODE_HYPHEN = chr(0x2010)
LEFT_DOUBLE_QUOTE = chr(0x201C)
RIGHT_DOUBLE_QUOTE = chr(0x201D)
RIGHT_SINGLE_QUOTE = chr(0x2019)


def test_invisible_characters_are_ignored() -> None:
    """Soft hyphen, zero-width space, zero-width joiners, word joiner and
    byte-order mark carry no meaning in a quote."""
    sents = _sentences(
        f"Soil{SOFT_HYPHEN}respiration{ZERO_WIDTH_SPACE}rose{ZERO_WIDTH_NON_JOINER} "
        f"by{ZERO_WIDTH_JOINER} 28{WORD_JOINER}%{BYTE_ORDER_MARK} under warming."
    )

    assert check_quote("Soil respiration rose by 28% under warming.", sents).state == MATCHES_SOURCE


def test_dashes_of_every_width_are_treated_as_one_character() -> None:
    sents = _sentences(
        f"Warming raised efflux 20{EN_DASH}30% in 2010{EM_DASH}2015 at "
        f"high{UNICODE_HYPHEN}latitude sites."
    )

    quote = "Warming raised efflux 20-30% in 2010-2015 at high-latitude sites."

    assert check_quote(quote, sents).state == MATCHES_SOURCE


def test_curly_quotes_match_straight_ones() -> None:
    sents = _sentences(
        f"The authors call this the {LEFT_DOUBLE_QUOTE}tipping point{RIGHT_DOUBLE_QUOTE} "
        f"of the system{RIGHT_SINGLE_QUOTE}s response."
    )

    quote = 'The authors call this the "tipping point" of the system\'s response.'

    assert check_quote(quote, sents).state == MATCHES_SOURCE


def test_non_breaking_and_repeated_whitespace_are_ignored() -> None:
    sents = _sentences(f"Efflux rose{NO_BREAK_SPACE}28%   under" + chr(10) + chr(9) + "warming.")

    assert check_quote("Efflux rose 28% under warming.", sents).state == MATCHES_SOURCE


def test_a_hyphen_that_belongs_to_the_text_is_kept_on_both_sides() -> None:
    """Only a hyphen between a word and a line break is dropped. One with a
    space before it, or next to punctuation, is text and must still match."""
    sents = _sentences("Values were 15.1 +/- 14.3 for the long-term plots.")

    kept = check_quote("Values were 15.1 +/- 14.3 for the long-term plots.", sents)
    altered = check_quote("Values were 15.1 +/ 14.3 for the longterm plots.", sents)

    assert kept.state == MATCHES_SOURCE
    assert altered.state == DIFFERS_FROM_SOURCE


def test_letter_case_is_not_normalized() -> None:
    """Deliberate: a quote that differs in case is a different quote."""
    sents = _sentences("Warming increased soil respiration.")

    assert check_quote("warming increased soil respiration.", sents).state == DIFFERS_FROM_SOURCE


def test_paper_without_sentences_is_unavailable_not_differing() -> None:
    """Not having the paper is not evidence against the quote."""
    result = check_quote("Anything at all.", [])

    assert result.state == SOURCE_UNAVAILABLE
    assert result.sentence_ids == ()


def test_normalize_keeps_letters_digits_and_punctuation() -> None:
    assert normalize("  Rose 28%,\tnot 82%. ") == "Rose28%,not82%."
