"""passages: source_check

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-15

Records, for every passage, whether its quote's words were found in this
server's own copy of the paper: matches_source, differs_from_source, or
source_unavailable (no text for that paper here). See domain/source_check.py
for how the comparison works and why a non-match is not proof of fabrication.

Existing passages are checked now rather than assumed. A passage built in this
app will match, because its words were copied from these sentences. A passage
from an older .ath import may point at sentence ids from the exporting server;
when its words are found here, its sentence_ids are re-pointed to this
server's sentences.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_STATES = ("matches_source", "differs_from_source", "source_unavailable")

# ── Frozen copy of domain/source_check.py as of this migration ──────────────
# Migrations must not import application code: if that module changes later,
# replaying this migration must still do exactly what it did when written.

# Code points, not literal characters: U+2010 is the Unicode hyphen and U+00AD
# (below) the invisible soft hyphen.
_LINE_BREAK_HYPHEN = re.compile("[-" + chr(0x2010) + r"]\s+")
_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace(chr(0x00AD), "")
    return _WHITESPACE.sub("", _LINE_BREAK_HYPHEN.sub("", text))


def _check(quote: str, sentences: list[tuple[uuid.UUID, str]]) -> tuple[str, list[uuid.UUID]]:
    """Return (state, covering sentence ids); see domain/source_check.check_quote."""
    if not sentences:
        return "source_unavailable", []
    target = _normalize(quote)
    if not target:
        return "differs_from_source", []
    pieces = [_normalize(text) for _sid, text in sentences]
    starts, offset = [], 0
    for piece in pieces:
        starts.append(offset)
        offset += len(piece)
    position = "".join(pieces).find(target)
    if position == -1:
        return "differs_from_source", []
    end = position + len(target)
    return "matches_source", [
        sid
        for (sid, _text), start, piece in zip(sentences, starts, pieces, strict=True)
        if start < end and start + len(piece) > position
    ]


# ────────────────────────────────────────────────────────────────────────────

_SET_STATE = sa.text("UPDATE passages SET source_check = :state WHERE id = :id")
_SET_STATE_AND_IDS = sa.text(
    "UPDATE passages SET source_check = :state, sentence_ids = :ids WHERE id = :id"
).bindparams(sa.bindparam("ids", type_=postgresql.ARRAY(postgresql.UUID(as_uuid=True))))


def upgrade() -> None:
    """Add the column, check every existing passage against its paper's
    sentences (loading each paper's sentences once), then make the column
    required and constrain it to the three states."""
    op.add_column("passages", sa.Column("source_check", sa.String(30), nullable=True))
    bind = op.get_bind()

    passages = bind.execute(
        sa.text("SELECT id, paper_id, sentence_ids, verbatim_text FROM passages")
    ).all()
    by_paper: dict[uuid.UUID, list[sa.Row]] = {}
    for p in passages:
        by_paper.setdefault(p.paper_id, []).append(p)

    counts: Counter[str] = Counter()
    repointed = 0
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
            if state == "matches_source" and list(covering) != list(p.sentence_ids or []):
                bind.execute(_SET_STATE_AND_IDS, {"state": state, "ids": covering, "id": p.id})
                repointed += 1
            else:
                bind.execute(_SET_STATE, {"state": state, "id": p.id})

    op.alter_column("passages", "source_check", nullable=False)
    op.create_check_constraint(
        "passages_source_check_chk",
        "passages",
        "source_check IN ('matches_source', 'differs_from_source', 'source_unavailable')",
    )

    log.info("0025: checked %d passages against this server's copy of their papers", len(passages))
    for state in _STATES:
        log.info("0025:   %s: %d", state, counts[state])
    log.info("0025: %d matching passages had sentence_ids re-pointed to this server's sentences",
             repointed)


def downgrade() -> None:
    """Drop the column. Sentence ids re-pointed by the upgrade are left as they
    are: the ids they replaced pointed at sentences that don't exist here."""
    bind = op.get_bind()
    flagged = bind.execute(
        sa.text("SELECT count(*) FROM passages WHERE source_check <> 'matches_source'")
    ).scalar_one()
    log.info("0025 downgrade: dropping source_check; %d passages lose a non-match flag", flagged)
    op.drop_constraint("passages_source_check_chk", "passages", type_="check")
    op.drop_column("passages", "source_check")
