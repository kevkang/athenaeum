"""add knowledge_links table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_type", sa.String(20), nullable=False),
        sa.Column("from_id", UUID(as_uuid=True), nullable=False),
        sa.Column("to_type", sa.String(20), nullable=False),
        sa.Column("to_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rel", sa.String(20), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_type IN ('paper','synthesis')",
            name="knowledge_links_from_type_chk",
        ),
        sa.CheckConstraint(
            "to_type IN ('paper','synthesis')",
            name="knowledge_links_to_type_chk",
        ),
        sa.CheckConstraint(
            "rel IN ('supports','contradicts','extends','cites','related')",
            name="knowledge_links_rel_chk",
        ),
        sa.UniqueConstraint(
            "from_id", "from_type", "to_id", "to_type", "rel",
            name="knowledge_links_uq",
        ),
    )
    op.create_index("ix_knowledge_links_project", "knowledge_links", ["project_id"])
    op.create_index("ix_knowledge_links_from", "knowledge_links", ["from_type", "from_id"])
    op.create_index("ix_knowledge_links_to", "knowledge_links", ["to_type", "to_id"])


def downgrade() -> None:
    op.drop_table("knowledge_links")
