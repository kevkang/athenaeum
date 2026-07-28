"""Add compose_documents (one per project, TipTap/ProseMirror JSON) and
highlights (personal yellow-bookmark spans in the reader pane).

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_DOC = '{"type": "doc", "content": [{"type": "paragraph"}]}'


def upgrade() -> None:
    op.create_table(
        "compose_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "content_json", JSONB(), nullable=False,
            server_default=sa.text(f"'{_EMPTY_DOC}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_unique_constraint(
        "compose_documents_project_id_uq", "compose_documents", ["project_id"]
    )

    op.create_table(
        "highlights",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "paper_id", UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("sentence_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("verbatim_text", sa.Text(), nullable=False),
        sa.Column(
            "author_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "highlights_project_paper_idx", "highlights", ["project_id", "paper_id"]
    )


def downgrade() -> None:
    op.drop_index("highlights_project_paper_idx", table_name="highlights")
    op.drop_table("highlights")
    op.drop_constraint(
        "compose_documents_project_id_uq", "compose_documents", type_="unique"
    )
    op.drop_table("compose_documents")
