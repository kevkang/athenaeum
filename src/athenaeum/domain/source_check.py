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

import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence
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

# Invisible characters that carry no meaning in a quote: the soft hyphen
# (U+00AD), zero-width space/non-joiner/joiner, word joiner, and byte-order
# mark. Written as code points so nothing invisible sits in this source file.
_INVISIBLE = frozenset({
    chr(0x00AD), chr(0x200B), chr(0x200C), chr(0x200D), chr(0x2060), chr(0xFEFF),
})

# Dash-like characters that typesetting and PDF extraction use interchangeably,
# all folded to the plain hyphen-minus: hyphen, non-breaking hyphen, figure
# dash, en dash, em dash, horizontal bar, minus sign.
_DASHES = dict.fromkeys(
    [chr(0x2010), chr(0x2011), chr(0x2012), chr(0x2013), chr(0x2014), chr(0x2015), chr(0x2212)],
    "-",
)

# Typographic quotation marks folded to their ASCII forms.
_QUOTES = {
    **dict.fromkeys([chr(0x2018), chr(0x2019), chr(0x201A), chr(0x201B), chr(0x2032)], "'"),
    **dict.fromkeys([chr(0x201C), chr(0x201D), chr(0x201E), chr(0x201F), chr(0x2033)], '"'),
}

_FOLDED = {**_DASHES, **_QUOTES}


def _is_line_break_hyphen(chars: list[str], i: int) -> bool:
    """True when the hyphen at `i` is a word broken across a line: a letter or
    digit, the hyphen, whitespace, then another letter or digit ("ef- flux" is
    "efflux"). A hyphen with a space before it ("a - b") or next to punctuation
    ("15.1 +/- 14.3") is left alone, so a dash between words and a hyphen that
    belongs to the text are never silently deleted."""
    if i == 0 or not chars[i - 1].isalnum():
        return False
    j = i + 1
    if j >= len(chars) or not chars[j].isspace():
        return False
    while j < len(chars) and chars[j].isspace():
        j += 1
    return j < len(chars) and chars[j].isalnum()


@dataclass(frozen=True)
class _Normalized:
    """One text normalized for matching, with enough bookkeeping to map a match
    back to where it came from.

    - `text`: the normalized characters, the only thing matching looks at.
    - `owners[i]`: which input part `text[i]` came from.
    - `positions[i]`: where `text[i]` sits in `document`.
    - `document`: the parts after NFKC, joined by single spaces. This is the
      text a TextQuoteSelector is drawn from (see text_quote_selector).
    """

    text: str
    owners: list[int]
    positions: list[int]
    document: str


def _normalize_parts(parts: Sequence[str]) -> _Normalized:
    """Normalize `parts` as one text joined by single spaces, recording which
    part each surviving character came from and where it sat.

    Normalizing the joined text, rather than each part on its own, is what keeps
    part boundaries honest. A source sentence can end in a hyphen with the next
    one continuing the text ("... 15.1 +/-" then "14.3 pg/mL"); once joined, that
    is a hyphen followed by a space, exactly as in a quote built from the same
    sentences, so both sides must drop or keep it the same way.
    """
    # NFKC first: it folds ligatures ("ﬁ"), subscript and superscript digits,
    # full-width letters and non-breaking spaces to plain forms. Applied per
    # part and then joined, which is identical to applying it to the joined
    # text: composition cannot reach across the joining space.
    normalized_parts = [unicodedata.normalize("NFKC", part) for part in parts]
    document = " ".join(normalized_parts)

    # First pass: drop invisible characters and fold look-alikes, keeping each
    # survivor's part and its position in `document`.
    chars: list[str] = []
    owners: list[int] = []
    positions: list[int] = []
    cursor = 0
    for index, part in enumerate(normalized_parts):
        if index:
            chars.append(" ")
            owners.append(-1)
            positions.append(cursor)
            cursor += 1
        for ch in part:
            if ch not in _INVISIBLE:
                chars.append(_FOLDED.get(ch, ch))
                owners.append(index)
                positions.append(cursor)
            cursor += 1

    # Second pass: drop whitespace and line-break hyphens. Done separately
    # because whether a hyphen is a line break depends on the characters after
    # it, which the first pass has to have settled.
    kept: list[str] = []
    kept_owners: list[int] = []
    kept_positions: list[int] = []
    for i, ch in enumerate(chars):
        if ch.isspace():
            continue
        if ch == "-" and _is_line_break_hyphen(chars, i):
            continue
        kept.append(ch)
        kept_owners.append(owners[i])
        kept_positions.append(positions[i])
    return _Normalized("".join(kept), kept_owners, kept_positions, document)


def _normalize_with_owners(parts: Sequence[str]) -> tuple[str, list[int]]:
    """Normalized text plus which part each character came from. Kept as its
    own name because the quote check only needs these two."""
    normalized = _normalize_parts(parts)
    return normalized.text, normalized.owners


def normalize(text: str) -> str:
    """Reduce text to the characters that must match, folding only the
    differences that come from how text was rendered or extracted.

    Normalized away: Unicode compatibility forms (ligatures, subscript and
    superscript digits, full-width letters, non-breaking spaces) via NFKC;
    invisible characters (soft hyphen, zero-width space, joiners, byte-order
    mark); dashes of every width, folded to a plain hyphen; curly quotes,
    folded to straight ones; hyphenation at a line break; and all whitespace,
    since extraction routinely splits or joins words ("CO 2" for "CO2").

    Deliberately kept, because a quote that differs here is a different quote:
    letter case, digits, punctuation inside a sentence, and hyphens that are
    part of the text ("long-term", "15.1 +/- 14.3").
    """
    return _normalize_with_owners([text])[0]


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

    # Normalize the paper's sentences as one joined text, remembering which
    # sentence every character came from, so a match maps back to sentences.
    haystack, owners = _normalize_with_owners([text for _sid, text in sentences])
    position = haystack.find(target)
    if position == -1:
        return SourceCheckResult(DIFFERS_FROM_SOURCE)

    touched = sorted(set(owners[position : position + len(target)]))
    return SourceCheckResult(MATCHES_SOURCE, tuple(sentences[i][0] for i in touched))


# ── W3C TextQuoteSelector ────────────────────────────────────────────────────

# Characters of context kept on each side of a quote. W3C sets no length; 32 is
# what Hypothesis uses and is enough to tell apart the repeated phrases that
# make a bare quote ambiguous ("n = 12", "as shown in Figure 2").
SELECTOR_CONTEXT_CHARS: Final = 32


def text_quote_selector(
    quote: str,
    sentences: Sequence[tuple[uuid.UUID | None, str]],
    prefer_sentence_ids: frozenset[uuid.UUID] = frozenset(),
) -> dict[str, str] | None:
    """Anchor `quote` in a paper's text as a W3C Web Annotation
    TextQuoteSelector: the quoted text plus the characters on either side.

    A selector is what lets a quote be found again somewhere other than this
    server — sentence ids are private to one database, but words are not. The
    prefix and suffix are what make that unambiguous when a quote's words occur
    more than once in the paper.

    The text is drawn from `sentences` joined by single spaces after NFKC
    (_Normalized.document), so exact, prefix and suffix use plain forms: "CO2"
    rather than "CO₂", "fi" rather than the ligature. Anyone re-finding a quote
    has to normalize anyway, and the quote as displayed stays untouched in the
    row's own verbatim_text.

    When the words occur more than once, the occurrence touching any sentence
    in `prefer_sentence_ids` wins — at creation time that is the sentences the
    quote was actually taken from. Otherwise the first occurrence, which is
    also the one check_quote points a quote at.

    Returns None when the quote cannot be found in the text. A selector must
    describe where a quote *is*, so one is never invented for a quote that
    differs from this server's copy.
    """
    if not sentences:
        return None
    target = normalize(quote)
    if not target:
        return None

    norm = _normalize_parts([text for _sid, text in sentences])
    starts: list[int] = []
    found = norm.text.find(target)
    while found != -1:
        starts.append(found)
        found = norm.text.find(target, found + 1)
    if not starts:
        return None

    chosen = starts[0]
    if prefer_sentence_ids:
        preferred = {i for i, (sid, _text) in enumerate(sentences) if sid in prefer_sentence_ids}
        for start in starts:
            if preferred.intersection(norm.owners[start : start + len(target)]):
                chosen = start
                break

    # Map the match back from normalized characters to the document. The span
    # runs from its first kept character to just past its last, so anything
    # normalization dropped in between (spaces, a line-break hyphen) stays in
    # `exact` where it belongs.
    begin = norm.positions[chosen]
    end = norm.positions[chosen + len(target) - 1] + 1
    return {
        "type": "TextQuoteSelector",
        "exact": norm.document[begin:end],
        "prefix": norm.document[max(0, begin - SELECTOR_CONTEXT_CHARS) : begin],
        "suffix": norm.document[end : end + SELECTOR_CONTEXT_CHARS],
    }


def text_quote_selector_in(quote: str, text: str) -> dict[str, str] | None:
    """The same selector, for a quote from a single text with no sentence
    split — an abstract, for instance."""
    return text_quote_selector(quote, [(None, text)])


# ── Applying the check to stored rows ────────────────────────────────────────


async def selector_for_new_quote(
    session: AsyncSession,
    paper_id: uuid.UUID,
    quote: str,
    sentence_ids: Sequence[uuid.UUID],
    cache: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] | None = None,
) -> dict[str, str] | None:
    """Selector for a quote just taken from this server's own sentences.

    The creation-time case, where the quote's sentence ids are known to be
    local and correct, so a repeated phrase is anchored to the place it was
    actually taken from rather than its first occurrence. `cache` lets a caller
    creating several quotes load each paper's sentences once.
    """
    if cache is not None and paper_id in cache:
        sentences = cache[paper_id]
    else:
        sentences = await load_paper_sentences(session, paper_id)
        if cache is not None:
            cache[paper_id] = sentences
    return text_quote_selector(quote, sentences, frozenset(sentence_ids))


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
    point at nothing here, and the passage is re-anchored with a selector.
    Otherwise its selector is cleared: a selector says where a quote is in this
    server's copy, so one that no longer holds must not survive."""
    result = check_quote(passage.verbatim_text, sentences)
    passage.source_check = result.state
    if result.state == MATCHES_SOURCE:
        passage.sentence_ids = list(result.sentence_ids)
        passage.text_quote_selector = text_quote_selector(passage.verbatim_text, sentences)
    else:
        passage.text_quote_selector = None
    return result.state


def repoint_highlight(highlight: Highlight, sentences: list[tuple[uuid.UUID, str]]) -> None:
    """Re-point a highlight at this server's sentences when its words are
    found, and re-anchor its selector the same way check_passage does.
    Highlights are personal bookmarks and store no check outcome."""
    result = check_quote(highlight.verbatim_text, sentences)
    if result.state == MATCHES_SOURCE:
        highlight.sentence_ids = list(result.sentence_ids)
        highlight.text_quote_selector = text_quote_selector(highlight.verbatim_text, sentences)
    else:
        highlight.text_quote_selector = None


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
