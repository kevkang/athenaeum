"""Merge questions table into bricks; add author_id column to bricks.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add author_id to bricks (nullable — existing rows have no author)
    op.add_column("bricks", sa.Column("author_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "bricks_author_id_fkey", "bricks", "users", ["author_id"], ["id"],
        ondelete="SET NULL",
    )

    # 2. Expand status check to cover question lifecycle values
    op.drop_constraint("bricks_status_chk", "bricks", type_="check")
    op.create_check_constraint(
        "bricks_status_chk",
        "bricks",
        "status IN ('draft','cemented','open','resolved','wontfix')",
    )

    # 3. Copy questions into bricks as type='question'
    op.execute("""
        INSERT INTO bricks
            (id, project_id, type, status, assertion, author_id, drafted_at,
             built_from_synthesis_id)
        SELECT
            id, project_id, 'question', status, body, author_id, created_at, NULL
        FROM questions
    """)

    # 4. Convert attached_to JSONB array → brick_rests_on edges
    op.execute("""
        INSERT INTO brick_rests_on (brick_id, target_kind, target_id, role)
        SELECT
            q.id,
            (elem->>'kind')::text,
            (elem->>'id')::uuid,
            'context'
        FROM questions q,
        LATERAL jsonb_array_elements(q.attached_to) AS elem
        WHERE jsonb_array_length(q.attached_to) > 0
          AND (elem->>'kind') IN ('paper','passage','dataset','brick')
          AND (elem->>'id') IS NOT NULL
        ON CONFLICT DO NOTHING
    """)

    # 5. Drop the now-redundant questions table
    op.drop_table("questions")


def downgrade() -> None:
    # Recreate questions table
    op.create_table(
        "questions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attached_to", JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint("status IN ('open','resolved','wontfix')", name="questions_status_chk"),
    )

    # Restore question rows from bricks (attached_to loses fidelity — edges are not restored)
    op.execute("""
        INSERT INTO questions (id, project_id, author_id, body, status, attached_to, created_at)
        SELECT
            id, project_id,
            COALESCE(author_id, '00000000-0000-0000-0000-000000000000'::uuid),
            assertion, status, '[]'::jsonb, drafted_at
        FROM bricks
        WHERE type = 'question'
    """)

    # Remove question bricks before restoring the narrower status constraint
    op.execute("DELETE FROM bricks WHERE type = 'question'")

    op.drop_constraint("bricks_status_chk", "bricks", type_="check")
    op.create_check_constraint(
        "bricks_status_chk", "bricks", "status IN ('draft','cemented')"
    )

    op.drop_constraint("bricks_author_id_fkey", "bricks", type_="foreignkey")
    op.drop_column("bricks", "author_id")
