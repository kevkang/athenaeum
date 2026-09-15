"""syntheses: relevance_rate, imported_at

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-15

Splits Synthesis's published measurement. `verification_rate` was the share of
passages a checker model judged both "plausibly verbatim" and "relevant". The
text of a synthesis passage is copied from stored sentences, so the verbatim
half measured nothing. New syntheses record `relevance_rate` instead. Past
syntheses can't be recomputed, because the two judgments were never stored
separately, so their relevance_rate stays NULL and verification_rate stays as
history.

`imported_at` marks syntheses that arrived in a .ath import, whose numbers were
measured on another server. Past imports are recognised by something only an
import produces: a finished synthesis with no stored pipeline steps. A synthesis
run here is created as 'running', only the pipeline sets 'done', and the
pipeline stores its steps as it goes; .ath archives carry no steps.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts below and then
rolls everything back (see migrations/env.py).
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_MARK_PAST_IMPORTS = sa.text(
    """
    UPDATE syntheses SET imported_at = now()
    WHERE status = 'done'
      AND NOT EXISTS (SELECT 1 FROM synthesis_steps st WHERE st.synthesis_id = syntheses.id)
    """
)


def upgrade() -> None:
    """Add both columns, then mark past imports. The original import time was
    never recorded, so migration time stands in for it; nothing reads the
    value itself, only whether it is set."""
    bind = op.get_bind()
    total = bind.execute(sa.text("SELECT count(*) FROM syntheses")).scalar_one()
    with_old_rate = bind.execute(
        sa.text("SELECT count(*) FROM syntheses WHERE verification_rate IS NOT NULL")
    ).scalar_one()

    op.add_column("syntheses", sa.Column("relevance_rate", sa.Float))
    op.add_column("syntheses", sa.Column("imported_at", sa.DateTime(timezone=True)))
    marked = bind.execute(_MARK_PAST_IMPORTS).rowcount

    log.info(
        "0024: %d syntheses get relevance_rate = NULL; %d keep a historical verification_rate",
        total, with_old_rate,
    )
    log.info("0024: %d syntheses recognised as past imports and marked imported", marked)


def downgrade() -> None:
    """Drop both columns. Any relevance_rate recorded since the upgrade is lost,
    so the count of affected rows is logged first."""
    bind = op.get_bind()
    losing = bind.execute(
        sa.text(
            "SELECT count(*) FROM syntheses "
            "WHERE relevance_rate IS NOT NULL OR imported_at IS NOT NULL"
        )
    ).scalar_one()
    log.info(
        "0024 downgrade: dropping relevance_rate and imported_at; %d syntheses lose a value",
        losing,
    )

    op.drop_column("syntheses", "imported_at")
    op.drop_column("syntheses", "relevance_rate")
