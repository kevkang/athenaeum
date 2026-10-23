"""audit_runs: quotes_checked, malformed_entries

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-15

Records, per audit run, how much evidence went to the checker and how many
entries in its responses could not be used.

Why this is worth a column: when the model returns an entry that doesn't fit
the tool schema, or an index that points at no quote, that quote gets no
verdict and is recorded as unsupported. On screen it is indistinguishable
from a citation the model judged and rejected. Counting the skips is what
lets anyone tell a run where the checker worked from one where it didn't.

Both columns are nullable and stay NULL on existing runs. Those runs never
counted, and writing 0 would state a clean response rate that nobody measured.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column("audit_runs", sa.Column("quotes_checked", sa.Integer(), nullable=True))
    op.add_column("audit_runs", sa.Column("malformed_entries", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "audit_runs_quotes_checked_chk",
        "audit_runs",
        "quotes_checked IS NULL OR quotes_checked >= 0",
    )
    op.create_check_constraint(
        "audit_runs_malformed_entries_chk",
        "audit_runs",
        "malformed_entries IS NULL OR (malformed_entries >= 0 "
        "AND malformed_entries <= quotes_checked)",
    )
    unmeasured = op.get_bind().execute(sa.text("SELECT count(*) FROM audit_runs")).scalar_one()
    log.info(
        "0028: %d existing audit runs keep NULL counts; they predate the instrumentation",
        unmeasured,
    )


def downgrade() -> None:
    bind = op.get_bind()
    measured = bind.execute(
        sa.text("SELECT count(*) FROM audit_runs WHERE quotes_checked IS NOT NULL")
    ).scalar_one()
    skipped = bind.execute(
        sa.text("SELECT coalesce(sum(malformed_entries), 0) FROM audit_runs")
    ).scalar_one()
    log.info(
        "0028 downgrade: dropping response counts from %d measured runs (%d recorded skips)",
        measured, skipped,
    )
    op.drop_constraint("audit_runs_malformed_entries_chk", "audit_runs", type_="check")
    op.drop_constraint("audit_runs_quotes_checked_chk", "audit_runs", type_="check")
    op.drop_column("audit_runs", "malformed_entries")
    op.drop_column("audit_runs", "quotes_checked")
