"""Add zotero_local_config singleton table, so the operator can pick their
Zotero data directory from the UI instead of a static env var + container
restart.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "zotero_local_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("relative_path", sa.Text, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="zotero_local_config_singleton_chk"),
    )


def downgrade() -> None:
    op.drop_table("zotero_local_config")
