"""highlights: zotero_annotation_key, position, sort_index, page_label

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-26

Captures where a Zotero highlight physically sits in its PDF. Until now the
import fetched Zotero's `position` and `sortIndex` and threw them away, keeping
only the fuzzy-matched sentence span — so a highlight could be shown in
Athenaeum's own reader but never handed back to a PDF reader, and the page
label the user saw in Zotero was lost.

Why this is time-sensitive rather than merely missing: an annotation edited or
deleted in Zotero afterwards takes its coordinates with it, and a user who
disconnects the account or moves libraries cannot get them back at all. Every
import that happens without this capture is unrecoverable.

Pure column addition — no existing row changes value, so the counts logged
below describe the backfill gap this leaves behind rather than rows touched.
`zotero_annotation_key` is what makes that gap closable later: a row that has
the key can have its position fetched again, a row without it cannot.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_COLUMNS = (
    "zotero_annotation_key",
    "zotero_position_json",
    "zotero_sort_index",
    "zotero_page_label",
)


def upgrade() -> None:
    """Add the four nullable provenance columns and report the backfill gap."""
    op.add_column("highlights", sa.Column("zotero_annotation_key", sa.String(50)))
    op.add_column("highlights", sa.Column("zotero_position_json", postgresql.JSONB))
    op.add_column("highlights", sa.Column("zotero_sort_index", sa.String(50)))
    op.add_column("highlights", sa.Column("zotero_page_label", sa.String(50)))

    bind = op.get_bind()
    total = bind.execute(sa.text("SELECT count(*) FROM highlights")).scalar_one()
    # Highlights on papers that came from Zotero: the subset that *should* have
    # position data and now provably doesn't. Highlights on non-Zotero papers
    # are not a gap — they were made in Athenaeum's reader and never had a PDF
    # annotation behind them.
    from_zotero_papers = bind.execute(
        sa.text(
            "SELECT count(*) FROM highlights h JOIN papers p ON p.id = h.paper_id "
            "WHERE p.zotero_item_key IS NOT NULL"
        )
    ).scalar_one()

    # Indexed because recognising an already-imported annotation is a lookup by
    # key per candidate, once per preview of a paper.
    op.create_index(
        "highlights_zotero_annotation_key_idx", "highlights", ["zotero_annotation_key"]
    )

    log.info("0029: %d highlights exist, none modified", total)
    log.info(
        "0029: %d of them are on Zotero-imported papers and have no position — "
        "not backfillable without the annotation key, which only new imports record",
        from_zotero_papers,
    )


def downgrade() -> None:
    """Drop the four columns; any position captured since the upgrade is lost."""
    bind = op.get_bind()
    captured = bind.execute(
        sa.text("SELECT count(*) FROM highlights WHERE zotero_position_json IS NOT NULL")
    ).scalar_one()
    log.info("0029 downgrade: discarding captured position for %d highlights", captured)

    op.drop_index("highlights_zotero_annotation_key_idx", table_name="highlights")
    for column in _COLUMNS:
        op.drop_column("highlights", column)
