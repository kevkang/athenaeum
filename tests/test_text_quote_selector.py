"""Tests for anchoring quotes as W3C TextQuoteSelectors.

The property that matters most: a selector describes where a quote *is*. Its
exact text must be the quote (modulo the same normalization the source check
applies), its prefix and suffix must be the real characters either side, and
no selector is produced for a quote that is not in the text.
"""
from __future__ import annotations

import uuid

import pytest

from athenaeum.domain.source_check import (
    SELECTOR_CONTEXT_CHARS,
    check_quote,
    normalize,
    text_quote_selector,
    text_quote_selector_in,
)


def _sentences(*texts: str) -> list[tuple[uuid.UUID, str]]:
    return [(uuid.uuid4(), t) for t in texts]


def _joined(sel: dict[str, str]) -> str:
    return sel["prefix"] + sel["exact"] + sel["suffix"]


# ── Shape ────────────────────────────────────────────────────────────────────

def test_selector_has_the_w3c_shape() -> None:
    sel = text_quote_selector("fix carbon", _sentences("These bacteria fix carbon at depth."))

    assert sel is not None
    assert set(sel) == {"type", "exact", "prefix", "suffix"}
    assert sel["type"] == "TextQuoteSelector"


def test_exact_prefix_and_suffix_are_contiguous_text() -> None:
    doc = _sentences("Vents host chemosynthetic bacteria.", "These bacteria fix carbon at depth.")

    sel = text_quote_selector("fix carbon", doc)

    assert sel is not None
    assert sel["exact"] == "fix carbon"
    assert "Vents host chemosynthetic bacteria. These bacteria fix carbon at depth.".find(
        _joined(sel)
    ) != -1


def test_context_is_capped_at_the_configured_length() -> None:
    long_before = "x" * 100
    long_after = "y" * 100
    sel = text_quote_selector("THE QUOTE", _sentences(f"{long_before} THE QUOTE {long_after}"))

    assert sel is not None
    assert len(sel["prefix"]) == SELECTOR_CONTEXT_CHARS
    assert len(sel["suffix"]) == SELECTOR_CONTEXT_CHARS


def test_context_is_shorter_at_the_edges_of_the_text() -> None:
    sel = text_quote_selector("Opening words", _sentences("Opening words of the paper."))

    assert sel is not None
    assert sel["prefix"] == ""
    assert sel["suffix"] == " of the paper."


# ── It describes where the quote actually is ─────────────────────────────────

@pytest.mark.parametrize(
    ("quote", "sentences"),
    [
        # A quote spanning two sentences, joined the way passages are built.
        ("fix carbon at depth. Tubeworms rely", ("These bacteria fix carbon at depth.",
                                                 "Tubeworms rely on this entirely.")),
        # A word broken across a line in the source.
        ("efflux rose sharply", ("Soil ef- flux rose sharply after warming.",)),
        # Typographic look-alikes: curly quotes and an en dash.
        (
            'the "hot spot" at 2-4 cm',
            ("They found the \u201chot spot\u201d at 2\u20134 cm depth.",),
        ),
        # Compatibility forms folded by NFKC.
        ("CO2 flux", ("Measured CO\u2082 flux doubled.",)),
    ],
)
def test_exact_is_the_quote_under_the_source_check_normalization(quote, sentences) -> None:
    """Whatever variation the source check forgives, the selector must still
    point at the same words — otherwise a quote could be 'found' by the check
    and anchored somewhere else."""
    doc = _sentences(*sentences)

    sel = text_quote_selector(quote, doc)

    assert sel is not None
    assert normalize(sel["exact"]) == normalize(quote)


def test_no_selector_for_a_quote_that_is_not_in_the_text() -> None:
    """A selector must never be invented for words this server does not have."""
    assert text_quote_selector("rose 82%", _sentences("Efflux rose 28% over the season.")) is None


def test_no_selector_without_text_or_without_a_quote() -> None:
    assert text_quote_selector("anything", []) is None
    assert text_quote_selector("   ", _sentences("Some text.")) is None


def test_selector_and_source_check_agree_on_whether_a_quote_is_present() -> None:
    """One normalization serves both, so 'found' and 'anchorable' can't drift."""
    doc = _sentences("Growth rates spanned two orders of magnitude.", "Weeks, not hours.")
    for quote in ("two orders of magnitude", "Weeks, not hours", "three orders", "hours of"):
        found = check_quote(quote, doc).state == "matches_source"
        assert found == (text_quote_selector(quote, doc) is not None), quote


# ── Repeated quotes ──────────────────────────────────────────────────────────

def test_repeated_quote_prefers_the_sentence_it_was_taken_from() -> None:
    """The same words at two places in a paper: the selector must anchor to the
    one the quote came from, and the prefix is what tells them apart."""
    first = (uuid.uuid4(), "In the control plots, n = 12 cores were taken.")
    second = (uuid.uuid4(), "In the warmed plots, n = 12 cores were taken.")

    default = text_quote_selector("n = 12 cores", [first, second])
    preferred = text_quote_selector(
        "n = 12 cores", [first, second], prefer_sentence_ids=frozenset({second[0]})
    )

    assert default is not None and preferred is not None
    assert "control" in default["prefix"]
    assert "warmed" in preferred["prefix"]


def test_preference_for_a_sentence_without_the_quote_falls_back_to_the_first() -> None:
    elsewhere = (uuid.uuid4(), "Unrelated opening sentence.")
    hit = (uuid.uuid4(), "The only occurrence is here.")

    sel = text_quote_selector(
        "only occurrence", [elsewhere, hit], prefer_sentence_ids=frozenset({elsewhere[0]})
    )

    assert sel is not None and sel["exact"] == "only occurrence"


# ── Single texts ─────────────────────────────────────────────────────────────

def test_selector_in_an_abstract() -> None:
    abstract = "We measured growth. Taxon-specific rates spanned two orders of magnitude."

    sel = text_quote_selector_in("spanned two orders", abstract)

    assert sel is not None
    assert sel["exact"] == "spanned two orders"
    assert sel["prefix"].endswith("Taxon-specific rates ")


# ── Wiring: re-checking and creation ─────────────────────────────────────────

def _passage(quote: str, sentence_ids=None):
    from athenaeum.storage.models import Passage

    return Passage(
        id=uuid.uuid4(), paper_id=uuid.uuid4(), sentence_ids=sentence_ids or [],
        verbatim_text=quote, source_check="source_unavailable",
    )


def test_check_passage_anchors_a_passage_it_finds() -> None:
    from athenaeum.domain.source_check import check_passage

    doc = _sentences("These bacteria fix carbon at depth.")
    passage = _passage("fix carbon")

    assert check_passage(passage, doc) == "matches_source"
    assert passage.text_quote_selector is not None
    assert passage.text_quote_selector["exact"] == "fix carbon"


def test_check_passage_clears_a_selector_that_no_longer_holds() -> None:
    """After re-ingestion replaces a paper's text, a quote that is no longer
    there must lose its selector — a stale one would point at words this
    server no longer has."""
    from athenaeum.domain.source_check import check_passage

    passage = _passage("fix carbon")
    passage.text_quote_selector = {"type": "TextQuoteSelector", "exact": "fix carbon",
                                   "prefix": "", "suffix": ""}

    assert check_passage(passage, _sentences("Entirely different text now.")) == (
        "differs_from_source"
    )
    assert passage.text_quote_selector is None


def test_repoint_highlight_sets_and_clears_its_selector() -> None:
    from athenaeum.domain.source_check import repoint_highlight
    from athenaeum.storage.models import Highlight

    highlight = Highlight(
        id=uuid.uuid4(), project_id=uuid.uuid4(), paper_id=uuid.uuid4(),
        sentence_ids=[], verbatim_text="fix carbon",
    )

    repoint_highlight(highlight, _sentences("These bacteria fix carbon at depth."))
    assert highlight.text_quote_selector is not None

    repoint_highlight(highlight, _sentences("Entirely different text now."))
    assert highlight.text_quote_selector is None


async def test_new_quote_is_anchored_where_it_was_taken(db_session) -> None:
    """Creation-time anchoring uses the quote's own sentence ids, so a phrase
    repeated in the paper is pinned to the occurrence actually quoted — not
    the first one, which is what a later re-check would fall back to."""
    from athenaeum.domain.source_check import selector_for_new_quote
    from athenaeum.storage.models import Paper, PaperSection, PaperSentence

    paper = Paper(id=uuid.uuid4(), title="Repeats", status="full_text")
    db_session.add(paper)
    await db_session.flush()
    section = PaperSection(id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0,
                           char_start=0, char_end=1)
    db_session.add(section)
    await db_session.flush()

    texts = ["In the control plots, n = 12 cores were taken.",
             "In the warmed plots, n = 12 cores were taken."]
    rows = []
    for i, text in enumerate(texts):
        row = PaperSentence(id=uuid.uuid4(), paper_id=paper.id, section_id=section.id,
                            ord=i, text=text, char_start=0, char_end=len(text))
        db_session.add(row)
        rows.append(row)
    await db_session.flush()

    cache: dict = {}
    sel = await selector_for_new_quote(
        db_session, paper.id, "n = 12 cores", [rows[1].id], cache
    )

    assert sel is not None and "warmed" in sel["prefix"]
    # The cache was filled, so a second quote from this paper costs no query.
    assert paper.id in cache
