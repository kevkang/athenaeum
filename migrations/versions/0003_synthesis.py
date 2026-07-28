"""synthesis tables: passages, syntheses, synthesis_evidence, synthesis_steps

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sentence_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("verbatim_text", sa.Text, nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False, server_default="FALSE"),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("checker_model", sa.String(100)),
    )

    op.create_table(
        "syntheses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_text", sa.Text, nullable=False),
        sa.Column("query_expansion_json", postgresql.JSONB),
        sa.Column(
            "retrieval_corpus",
            sa.String(20),
            nullable=False,
            server_default="project",
        ),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("checker_model", sa.String(100)),
        sa.Column("verification_rate", sa.Float),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')", name="syntheses_status_chk"
        ),
    )
    op.create_index("syntheses_project_idx", "syntheses", ["project_id"])

    op.create_table(
        "synthesis_evidence",
        sa.Column(
            "synthesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("syntheses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "passage_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("passages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "role IN ('theory','opposing','framework','stakes','method','data')",
            name="synthesis_evidence_role_chk",
        ),
    )

    op.create_table(
        "synthesis_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "synthesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("syntheses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload_json", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('query_interp','retrieval','extract','signal','verify')",
            name="synthesis_steps_kind_chk",
        ),
    )
    op.create_index(
        "synthesis_steps_synthesis_ord_idx",
        "synthesis_steps",
        ["synthesis_id", "ord"],
    )


def downgrade() -> None:
    op.drop_table("synthesis_steps")
    op.drop_table("synthesis_evidence")
    op.drop_table("syntheses")
    op.drop_table("passages")
