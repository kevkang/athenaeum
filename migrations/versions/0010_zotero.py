"""Zotero integration: per-user credentials + paper provenance columns.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zotero_credentials",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("zotero_user_id", sa.String(50), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("library_type", sa.String(20), nullable=False, server_default="user"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "library_type IN ('user','group')", name="zotero_credentials_library_type_chk",
        ),
    )

    op.add_column("papers", sa.Column("zotero_item_key", sa.String(50), nullable=True))
    op.create_index(
        "ix_papers_zotero_item_key", "papers", ["zotero_item_key"], unique=True
    )

    op.add_column("papers", sa.Column("zotero_imported_by", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "papers_zotero_imported_by_fkey",
        "papers",
        "users",
        ["zotero_imported_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("papers_zotero_imported_by_fkey", "papers", type_="foreignkey")
    op.drop_column("papers", "zotero_imported_by")

    op.drop_index("ix_papers_zotero_item_key", table_name="papers")
    op.drop_column("papers", "zotero_item_key")

    op.drop_table("zotero_credentials")
