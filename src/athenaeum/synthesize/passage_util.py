"""Shared helper for turning retrieved PaperSentence rows into a verbatim
Passage quote — used by both orchestrator.py's synthesis pipeline and
backtrace.py's single-hop extraction, which otherwise duplicate this exact
fetch-then-join step."""
from __future__ import annotations

from itertools import pairwise


def is_contiguous(ords: list[int]) -> bool:
    """True if `ords` — the `ord` values of a set of selected PaperSentence
    rows, already sorted ascending by an `ORDER BY ord` query — has no gaps.

    verbatim_text is built by joining sentence text in `ord` order. If the
    selected sentences aren't actually contiguous, that join still reads as
    one continuous quote, but silently skips whatever sentence(s) sit in the
    gap — which could be a caveat, a negation, or anything else that changes
    the meaning. The extraction prompt *asks* the model to select consecutive
    sentences; this is what makes "verbatim" structurally true rather than
    merely requested.
    """
    if len(ords) <= 1:
        return True
    return all(b - a == 1 for a, b in pairwise(ords))
