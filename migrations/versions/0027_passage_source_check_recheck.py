"""passages: source_check_corrected

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-15

Re-checks every passage currently marked `differs_from_source` with the
corrected comparison, and records the result in a new column beside the
original verdict.

Why: the comparison that produced those verdicts (frozen in migration 0025)
normalized each sentence on its own. A source sentence ending in a hyphen —
"... treated with 5-" / "fluorouracil ..." — became "...5-" followed by
"fluorouracil...", while the same words quoted as one span became
"...5fluorouracil...", so a genuine quote was flagged as differing. The
corrected comparison (domain/source_check.py) normalizes the sentences as one
joined text, and also folds dash and quote variants that PDF extraction
introduces.

Why a new column rather than an update in place: `differs_from_source` is a
claim about a person's quote. Overwriting it would erase the evidence that the
first verdict was wrong, and there is no way to tell afterwards which rows the
old bug produced. Both verdicts stay visible, and a later step decides what to
do with the difference.

This migration writes nothing except the new column: sentence_ids on rows that
now match are left pointing where they pointed, and the upgrade logs how many
would be re-pointed if the corrected verdicts were accepted.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_STATES = ("matches_source", "differs_from_source", "source_unavailable")

# ── Frozen copy of domain/source_check.py as of this migration ──────────────
# Migrations must not import application code: if that module changes again,
# replaying this migration must still do exactly what it did when written.
# Characters are written as code points so nothing invisible sits in this file.

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


def _is_line_break_hyphen(chars: list[str], i: int) -> bool:
    if i == 0 or not chars[i - 1].isalnum():
        return False
    j = i + 1
    if j >= len(chars) or not chars[j].isspace():
        return False
    while j < len(chars) and chars[j].isspace():
        j += 1
    return j < len(chars) and chars[j].isalnum()


def _normalize_with_owners(parts: Sequence[str]) -> tuple[str, list[int]]:
    chars: list[str] = []
    owners: list[int] = []
    for index, part in enumerate(parts):
        if index:
            chars.append(" ")
            owners.append(-1)
        for ch in unicodedata.normalize("NFKC", part):
            if ch in _INVISIBLE:
                continue
            chars.append(_FOLDED.get(ch, ch))
            owners.append(index)

    kept: list[str] = []
    kept_owners: list[int] = []
    for i, ch in enumerate(chars):
        if ch.isspace():
            continue
        if ch == "-" and _is_line_break_hyphen(chars, i):
            continue
        kept.append(ch)
        kept_owners.append(owners[i])
    return "".join(kept), kept_owners


def _check(quote: str, sentences: list[tuple[uuid.UUID, str]]) -> tuple[str, list[uuid.UUID]]:
    """Return (state, covering sentence ids); see domain/source_check.check_quote."""
    if not sentences:
        return "source_unavailable", []
    target = _normalize_with_owners([quote])[0]
    if not target:
        return "differs_from_source", []
    haystack, owners = _normalize_with_owners([text for _sid, text in sentences])
    position = haystack.find(target)
    if position == -1:
        return "differs_from_source", []
    touched = sorted(set(owners[position : position + len(target)]))
    return "matches_source", [sentences[i][0] for i in touched]


# ────────────────────────────────────────────────────────────────────────────

_SET_CORRECTED = sa.text(
    "UPDATE passages SET source_check_corrected = :state WHERE id = :id"
)


def upgrade() -> None:
    """Add the column, then re-check the passages that the old comparison
    flagged, loading each paper's sentences once.

    Only `differs_from_source` rows are re-checked: `matches_source` is a
    positive find that the corrected comparison, which folds strictly more
    characters, cannot turn into a non-match, and `source_unavailable` means
    the paper has no text here, which no comparison changes.
    """
    op.add_column("passages", sa.Column("source_check_corrected", sa.String(30), nullable=True))
    op.create_check_constraint(
        "passages_source_check_corrected_chk",
        "passages",
        "source_check_corrected IS NULL OR source_check_corrected IN "
        "('matches_source', 'differs_from_source', 'source_unavailable')",
    )

    bind = op.get_bind()
    flagged = bind.execute(
        sa.text(
            "SELECT id, paper_id, sentence_ids, verbatim_text FROM passages "
            "WHERE source_check = 'differs_from_source' ORDER BY id"
        )
    ).all()
    by_paper: dict[uuid.UUID, list[sa.Row]] = {}
    for p in flagged:
        by_paper.setdefault(p.paper_id, []).append(p)

    counts: Counter[str] = Counter()
    would_repoint = 0
    for paper_id, paper_passages in by_paper.items():
        sentences = [
            (row.id, row.text)
            for row in bind.execute(
                sa.text("SELECT id, text FROM paper_sentences WHERE paper_id = :pid ORDER BY ord"),
                {"pid": paper_id},
            )
        ]
        for p in paper_passages:
            state, covering = _check(p.verbatim_text, sentences)
            counts[state] += 1
            bind.execute(_SET_CORRECTED, {"state": state, "id": p.id})
            if state == "matches_source":
                # Reported, not applied: accepting a corrected verdict is a
                # separate decision, and re-pointing is part of accepting it.
                if list(covering) != list(p.sentence_ids or []):
                    would_repoint += 1
                log.info(
                    "0027:   passage %s now matches its source (paper %s): %.80s",
                    p.id, p.paper_id, p.verbatim_text.replace("\n", " "),
                )

    changed = len(flagged) - counts["differs_from_source"]
    log.info("0027: re-checked %d passages marked differs_from_source", len(flagged))
    for state in _STATES:
        log.info("0027:   corrected verdict %s: %d", state, counts[state])
    log.info("0027: %d of %d would change verdict; original source_check left untouched",
             changed, len(flagged))
    log.info("0027: %d of those would need sentence_ids re-pointed when accepted", would_repoint)


def downgrade() -> None:
    """Drop the column. Nothing else was modified, so the rows return to
    exactly the state the upgrade found them in."""
    bind = op.get_bind()
    corrected = bind.execute(
        sa.text(
            "SELECT count(*) FROM passages WHERE source_check_corrected IS NOT NULL "
            "AND source_check_corrected <> source_check"
        )
    ).scalar_one()
    log.info("0027 downgrade: dropping source_check_corrected; %d corrected verdicts discarded",
             corrected)
    op.drop_constraint("passages_source_check_corrected_chk", "passages", type_="check")
    op.drop_column("passages", "source_check_corrected")
