"""Add papers.zotero_source ('sync' | 'local') so /papers/{id}/retry knows
which Zotero path (Web API credentials vs. local data directory) to re-run.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("zotero_source", sa.String(10), nullable=True))
    op.execute("UPDATE papers SET zotero_source = 'sync' WHERE zotero_item_key IS NOT NULL")
    op.create_check_constraint(
        "papers_zotero_source_chk",
        "papers",
        "zotero_source IN ('sync', 'local')",
    )


def downgrade() -> None:
    op.drop_constraint("papers_zotero_source_chk", "papers", type_="check")
    op.drop_column("papers", "zotero_source")
