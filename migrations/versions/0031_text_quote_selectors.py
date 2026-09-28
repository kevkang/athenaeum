"""passages, highlights: text_quote_selector (W3C Web Annotation)

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-27

Adds a W3C TextQuoteSelector — {type, exact, prefix, suffix} — to every
stored quote, and backfills it from this server's own copy of each paper.

Why now (NUCLEATE_PLAN.md, Phase 3.2): the archivist is about to generate
quotes at scale, and selectors should exist before that data does rather than
be backfilled after. Sentence ids mean nothing outside this database; a
selector anchors a quote by its words, which survive export, a different PDF,
or a re-extraction.

Backfill rules:

- A passage is anchored only when its recorded source_check is
  'matches_source'. Migration 0027 found quotes the original check had wrongly
  flagged, and deliberately left both verdicts side by side for a later
  decision. Anchoring those here would have the selector say "it is here"
  while the recorded verdict says "it differs", pre-empting that decision.
- Where a quote's words occur more than once, the occurrence touching its own
  sentence_ids is used, since those ids are local and valid.
- Highlights carry no check verdict, so any highlight whose words are found is
  anchored.

The selector logic below is a frozen copy of domain/source_check.py as it was
when this migration was written, per this project's convention (see 0025 and
0027): a migration must produce the same result forever, even after the live
code changes. tests/test_text_quote_selector_migration.py checks the copy
still matches the live function.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import json
import logging
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


# ── Frozen copy of domain/source_check.py's selector logic ────────────────────

_INVISIBLE = frozenset({
    chr(0x00AD), chr(0x200B), chr(0x200C), chr(0x200D), chr(0x2060), chr(0xFEFF),
})
_DASHES = dict.fromkeys(
    [chr(0x2010), chr(0x2011), chr(0x2012), chr(0x2013), chr(0x2014), chr(0x2015), chr(0x2212)],
    "-",
)
_QUOTES = {
    **dict.fromkeys([chr(0x2018), chr(0x2019), chr(0x201A), chr(0x201B), chr(0x2032)], "'"),
    **dict.fromkeys([chr(0x201C), chr(0x201D), chr(0x201E), chr(0x201F), chr(0x2033)], '"'),
}
_FOLDED = {**_DASHES, **_QUOTES}
_CONTEXT = 32


def _is_line_break_hyphen(chars: list[str], i: int) -> bool:
    if i == 0 or not chars[i - 1].isalnum():
        return False
    j = i + 1
    if j >= len(chars) or not chars[j].isspace():
        return False
    while j < len(chars) and chars[j].isspace():
        j += 1
    return j < len(chars) and chars[j].isalnum()


def _normalize_parts(parts: Sequence[str]) -> tuple[str, list[int], list[int], str]:
    normalized_parts = [unicodedata.normalize("NFKC", part) for part in parts]
    document = " ".join(normalized_parts)
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
    return "".join(kept), kept_owners, kept_positions, document


def text_quote_selector(
    quote: str,
    sentences: Sequence[tuple[Any, str]],
    prefer_sentence_ids: frozenset[Any] = frozenset(),
) -> dict[str, str] | None:
    if not sentences:
        return None
    target = _normalize_parts([quote])[0]
    if not target:
        return None
    text, owners, positions, document = _normalize_parts([t for _sid, t in sentences])
    starts: list[int] = []
    found = text.find(target)
    while found != -1:
        starts.append(found)
        found = text.find(target, found + 1)
    if not starts:
        return None
    chosen = starts[0]
    if prefer_sentence_ids:
        preferred = {i for i, (sid, _t) in enumerate(sentences) if sid in prefer_sentence_ids}
        for start in starts:
            if preferred.intersection(owners[start : start + len(target)]):
                chosen = start
                break
    begin = positions[chosen]
    end = positions[chosen + len(target) - 1] + 1
    return {
        "type": "TextQuoteSelector",
        "exact": document[begin:end],
        "prefix": document[max(0, begin - _CONTEXT) : begin],
        "suffix": document[end : end + _CONTEXT],
    }


# ── Migration ────────────────────────────────────────────────────────────────

_CHECK = "text_quote_selector IS NULL OR text_quote_selector->>'type' = 'TextQuoteSelector'"


def _sentences_by_paper(bind: Any) -> dict[uuid.UUID, list[tuple[uuid.UUID, str]]]:
    """Every paper's sentences in document order, loaded once."""
    grouped: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    rows = bind.execute(
        sa.text("SELECT paper_id, id, text FROM paper_sentences ORDER BY paper_id, ord")
    )
    for paper_id, sentence_id, text in rows:
        grouped.setdefault(paper_id, []).append((sentence_id, text))
    return grouped


def upgrade() -> None:
    op.add_column("passages", sa.Column("text_quote_selector", postgresql.JSONB))
    op.add_column("highlights", sa.Column("text_quote_selector", postgresql.JSONB))
    op.create_check_constraint("passages_text_quote_selector_chk", "passages", _CHECK)
    op.create_check_constraint("highlights_text_quote_selector_chk", "highlights", _CHECK)

    bind = op.get_bind()
    sentences = _sentences_by_paper(bind)
    set_passage = sa.text(
        "UPDATE passages SET text_quote_selector = CAST(:selector AS jsonb) WHERE id = :id"
    )
    set_highlight = sa.text(
        "UPDATE highlights SET text_quote_selector = CAST(:selector AS jsonb) WHERE id = :id"
    )

    passages: Counter[str] = Counter()
    for row in bind.execute(
        sa.text("SELECT id, paper_id, sentence_ids, verbatim_text, source_check FROM passages")
    ):
        if row.source_check != "matches_source":
            passages["skipped: not matches_source"] += 1
            continue
        selector = text_quote_selector(
            row.verbatim_text,
            sentences.get(row.paper_id, []),
            frozenset(row.sentence_ids or ()),
        )
        if selector is None:
            # Recorded as matching but not locatable now: the paper's text was
            # replaced since, or the two diverged. Left NULL rather than guessed.
            passages["not locatable despite matches_source"] += 1
            continue
        bind.execute(set_passage, {"selector": json.dumps(selector), "id": row.id})
        passages["anchored"] += 1

    highlights: Counter[str] = Counter()
    for row in bind.execute(
        sa.text("SELECT id, paper_id, sentence_ids, verbatim_text FROM highlights")
    ):
        selector = text_quote_selector(
            row.verbatim_text,
            sentences.get(row.paper_id, []),
            frozenset(row.sentence_ids or ()),
        )
        if selector is None:
            highlights["not locatable"] += 1
            continue
        bind.execute(set_highlight, {"selector": json.dumps(selector), "id": row.id})
        highlights["anchored"] += 1

    for label, count in sorted(passages.items()):
        log.info("0031 passages: %d %s", count, label)
    for label, count in sorted(highlights.items()):
        log.info("0031 highlights: %d %s", count, label)
    if not passages and not highlights:
        log.info("0031: no passages or highlights to anchor")


def downgrade() -> None:
    """Drop both columns; every selector, backfilled or written since, is lost."""
    bind = op.get_bind()
    for table in ("passages", "highlights"):
        n = bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE text_quote_selector IS NOT NULL")
        ).scalar_one()
        log.info("0031 downgrade: discarding %d %s selectors", n, table)
    op.drop_constraint("highlights_text_quote_selector_chk", "highlights", type_="check")
    op.drop_constraint("passages_text_quote_selector_chk", "passages", type_="check")
    op.drop_column("highlights", "text_quote_selector")
    op.drop_column("passages", "text_quote_selector")
