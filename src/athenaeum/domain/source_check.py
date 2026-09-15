"""Checks a quote against this server's own copy of the paper it cites.

Two servers split the same PDF into sentences independently, so sentence ids
never line up between them; the comparison has to be on the words. Text
extraction also varies in harmless ways (spacing, a hyphen where a word broke
across a line, "CO₂" versus "CO2"), so both sides are normalized first. Nothing
else is forgiven: every letter and digit must match, in order, so "rose 28%"
never matches "rose 82%".

A non-match therefore means the quote *differs from this server's copy*, not
that anyone fabricated it. An innocent extraction difference can produce one,
which is why a differing quote is flagged for a person rather than rejected.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.storage.models import Highlight, PaperSentence, Passage

# Final tells mypy each constant is exactly its literal value, so the
# constants satisfy the SourceCheck type below.
MATCHES_SOURCE: Final = "matches_source"
DIFFERS_FROM_SOURCE: Final = "differs_from_source"
SOURCE_UNAVAILABLE: Final = "source_unavailable"

SourceCheck = Literal["matches_source", "differs_from_source", "source_unavailable"]
SOURCE_CHECK_STATES: tuple[SourceCheck, ...] = (
    MATCHES_SOURCE,
    DIFFERS_FROM_SOURCE,
    SOURCE_UNAVAILABLE,
)

# Characters are written as code points so nothing invisible or lookalike sits
# in the source: U+00AD is the soft hyphen, U+2010 the Unicode hyphen.
_SOFT_HYPHEN = chr(0x00AD)
# A hyphen immediately followed by whitespace is a word broken across a line:
# "ef- flux" is "efflux". Covers ASCII "-" and the Unicode hyphen, which NFKC
# leaves in place (the non-breaking hyphen folds to it).
_LINE_BREAK_HYPHEN = re.compile("[-" + chr(0x2010) + r"]\s+")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Reduce text to the characters that must match.

    NFKC folds compatibility forms (subscript digits, ligatures such as "ﬁ",
    full-width letters) to their plain equivalents. Soft hyphens and
    line-break hyphens are dropped, then all whitespace is removed, so spacing
    and line-wrapping can never cause a mismatch while every other character
    still has to agree.
    """
    text = unicodedata.normalize("NFKC", text).replace(_SOFT_HYPHEN, "")
    text = _LINE_BREAK_HYPHEN.sub("", text)
    return _WHITESPACE.sub("", text)


@dataclass(frozen=True)
class SourceCheckResult:
    state: SourceCheck
    # This server's sentences that contain the quote, in document order.
    # Empty unless state is matches_source.
    sentence_ids: tuple[uuid.UUID, ...] = ()


def check_quote(quote: str, sentences: list[tuple[uuid.UUID, str]]) -> SourceCheckResult:
    """Look for `quote` in one paper's sentences, given in document order.

    - No sentences: this server has no text for the paper, so
      source_unavailable.
    - The normalized quote occurs in the normalized text: matches_source, with
      the ids of every sentence the match touches. The match may start or end
      mid-sentence, since the exporting server may have split sentences
      differently.
    - Otherwise differs_from_source. An empty quote can't be confirmed against
      anything, so it also counts as differing.
    """
    if not sentences:
        return SourceCheckResult(SOURCE_UNAVAILABLE)

    target = normalize(quote)
    if not target:
        return SourceCheckResult(DIFFERS_FROM_SOURCE)

    # Normalize sentence by sentence, recording where each one starts in the
    # joined string, so a match position can be mapped back to sentences.
    pieces = [normalize(text) for _sid, text in sentences]
    starts: list[int] = []
    offset = 0
    for piece in pieces:
        starts.append(offset)
        offset += len(piece)

    position = "".join(pieces).find(target)
    if position == -1:
        return SourceCheckResult(DIFFERS_FROM_SOURCE)

    end = position + len(target)
    covering = tuple(
        sid
        for (sid, _text), start, piece in zip(sentences, starts, pieces, strict=True)
        if start < end and start + len(piece) > position
    )
    return SourceCheckResult(MATCHES_SOURCE, covering)


# ── Applying the check to stored rows ────────────────────────────────────────


async def load_paper_sentences(
    session: AsyncSession, paper_id: uuid.UUID
) -> list[tuple[uuid.UUID, str]]:
    """This server's sentences for one paper, in document order, in the shape
    check_quote expects."""
    rows = await session.execute(
        select(PaperSentence.id, PaperSentence.text)
        .where(PaperSentence.paper_id == paper_id)
        .order_by(PaperSentence.ord)
    )
    return [(sid, text) for sid, text in rows.tuples()]


def check_passage(passage: Passage, sentences: list[tuple[uuid.UUID, str]]) -> SourceCheck:
    """Check a passage's quote against its paper's sentences on this server and
    record the outcome on the passage. When the words are found, sentence_ids
    are re-pointed at this server's sentences, since ids from another server
    point at nothing here."""
    result = check_quote(passage.verbatim_text, sentences)
    passage.source_check = result.state
    if result.state == MATCHES_SOURCE:
        passage.sentence_ids = list(result.sentence_ids)
    return result.state


def repoint_highlight(highlight: Highlight, sentences: list[tuple[uuid.UUID, str]]) -> None:
    """Re-point a highlight at this server's sentences when its words are
    found. Highlights are personal bookmarks and store no check outcome."""
    result = check_quote(highlight.verbatim_text, sentences)
    if result.state == MATCHES_SOURCE:
        highlight.sentence_ids = list(result.sentence_ids)


async def recheck_paper(session: AsyncSession, paper_id: uuid.UUID) -> Counter[str]:
    """Re-check every passage quoting a paper, and re-point its highlights,
    after this server gains or replaces the paper's text.

    Called at the end of ingestion; this is how quotes imported while their
    paper was missing get checked once it arrives. Every passage is re-checked,
    not only unmatched ones, because re-ingesting a paper replaces its
    sentences and would leave matched passages pointing at old ids. Returns
    how many passages landed in each state.
    """
    sentences = await load_paper_sentences(session, paper_id)
    counts: Counter[str] = Counter()
    for passage in await session.scalars(select(Passage).where(Passage.paper_id == paper_id)):
        counts[check_passage(passage, sentences)] += 1
    for highlight in await session.scalars(select(Highlight).where(Highlight.paper_id == paper_id)):
        repoint_highlight(highlight, sentences)
    return counts
