"""Matches a Zotero annotation's verbatim highlighted text against a paper's
own sentence-parsed body, to anchor it as an Athenaeum Highlight the same way
a hand-drawn reader-pane selection is (see storage/models.py's Highlight,
which is keyed on `sentence_ids`, not a free-floating string).

Zotero's own PDF text extraction reflows and hyphenates differently than
Athenaeum's ingestion pipeline, so this is necessarily a fuzzy match, not an
equality check — it scores candidate sentence windows by similarity and
reconstructs `verbatim_text` from Athenaeum's own sentence text, never from
the Zotero string itself, so a Highlight's `verbatim_text` is always
guaranteed self-consistent with its `sentence_ids`.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.storage.models import PaperSentence

# Below this similarity, a "match" is more likely coincidental word overlap
# between unrelated sentences than the same underlying quote — skip it
# rather than create a misleading highlight.
MIN_MATCH_RATIO = 0.6

# How many sentences beyond the best single-sentence anchor to consider
# growing into. A Zotero highlight rarely spans more than a handful of
# sentences, and unbounded growth risks pulling in unrelated text that just
# happens to share vocabulary with the target.
MAX_WINDOW_RADIUS = 4


@dataclass
class HighlightMatch:
    sentence_ids: list[uuid.UUID]
    verbatim_text: str
    ratio: float


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


async def match_annotation_to_sentences(
    session: AsyncSession, paper_id: uuid.UUID, annotation_text: str
) -> HighlightMatch | None:
    target = _normalize(annotation_text)
    if not target:
        return None

    sentences = list(
        await session.scalars(
            select(PaperSentence)
            .where(PaperSentence.paper_id == paper_id)
            .order_by(PaperSentence.ord)
        )
    )
    if not sentences:
        return None

    def ratio_of(lo: int, hi: int) -> float:
        window = _normalize(" ".join(s.text for s in sentences[lo : hi + 1]))
        return SequenceMatcher(None, target, window).ratio()

    # Anchor on the single sentence most similar to the target, then grow
    # the window outward while growth keeps improving the match — cheaper
    # than scoring every possible contiguous span, and good enough since a
    # quote's most distinctive sentence is rarely at its very edge.
    best_anchor = max(range(len(sentences)), key=lambda i: ratio_of(i, i))
    best_lo = best_hi = best_anchor
    best_ratio = ratio_of(best_anchor, best_anchor)

    lo, hi = best_anchor, best_anchor
    for _ in range(MAX_WINDOW_RADIUS):
        candidates: list[tuple[int, int]] = []
        if lo > 0:
            candidates.append((lo - 1, hi))
        if hi < len(sentences) - 1:
            candidates.append((lo, hi + 1))
        if not candidates:
            break

        new_lo, new_hi = max(candidates, key=lambda span: ratio_of(*span))
        ratio = ratio_of(new_lo, new_hi)
        lo, hi = new_lo, new_hi
        if ratio > best_ratio:
            best_ratio, best_lo, best_hi = ratio, lo, hi
        elif ratio < best_ratio - 0.02:
            # Growth is making things worse, not better — stop rather than
            # keep pulling in unrelated sentences hoping it recovers.
            break

    if best_ratio < MIN_MATCH_RATIO:
        return None

    matched = sentences[best_lo : best_hi + 1]
    return HighlightMatch(
        sentence_ids=[s.id for s in matched],
        verbatim_text=" ".join(s.text for s in matched),
        ratio=best_ratio,
    )
