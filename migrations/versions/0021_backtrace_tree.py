"""Turn backtrace from a strictly linear chain into a tree: add
backtrace_hops.parent_hop_id (self-referencing) so a hop can have several
children, letting a user explore multiple citations from one claim as
parallel branches instead of only ever continuing from "the last hop."

Existing chains are strictly linear today (hop_index = 0, 1, 2, ... with no
gaps, enforced purely by construction rather than a DB constraint), so this
also backfills parent_hop_id for every existing row by reconstructing that
linear order — without this, every historical chain would render as N
disconnected root hops instead of one branch once hops are read as a tree.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "backtrace_hops",
        sa.Column(
            "parent_hop_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backtrace_hops.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.create_index("backtrace_hops_parent_idx", "backtrace_hops", ["parent_hop_id"])

    op.execute(
        """
        UPDATE backtrace_hops h
        SET parent_hop_id = p.id
        FROM backtrace_hops p
        WHERE p.chain_id = h.chain_id AND p.hop_index = h.hop_index - 1
        """
    )


def downgrade() -> None:
    op.drop_index("backtrace_hops_parent_idx", table_name="backtrace_hops")
    op.drop_column("backtrace_hops", "parent_hop_id")
