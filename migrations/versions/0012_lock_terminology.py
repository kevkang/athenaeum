"""Rename statement 'cemented' status/terminology to 'locked'. Locking is not
immutable — a statement can be unlocked again at any time (via an explicit
unlock action or implicitly by editing it).

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-12

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Constraint must be dropped before the data migration — the old CHECK
    # doesn't allow 'locked' yet, so updating rows into it first would violate
    # statements_status_chk.
    op.drop_constraint("statements_status_chk", "statements", type_="check")
    op.execute("UPDATE statements SET status = 'locked' WHERE status = 'cemented'")
    op.create_check_constraint(
        "statements_status_chk",
        "statements",
        "status IN ('draft','locked','open','resolved','wontfix')",
    )
    op.alter_column("statements", "cemented_at", new_column_name="locked_at")


def downgrade() -> None:
    op.alter_column("statements", "locked_at", new_column_name="cemented_at")
    op.drop_constraint("statements_status_chk", "statements", type_="check")
    op.execute("UPDATE statements SET status = 'cemented' WHERE status = 'locked'")
    op.create_check_constraint(
        "statements_status_chk",
        "statements",
        "status IN ('draft','cemented','open','resolved','wontfix')",
    )
