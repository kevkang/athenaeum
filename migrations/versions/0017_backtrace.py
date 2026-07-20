"""Add backtrace_chains/backtrace_hops — a persisted, user-driven citation
chain: starting from a claim in a paper the user is reading, each hop records
which of that paper's own OpenAlex references was identified as supporting
the same claim, the verbatim quote found there (abstract or full text), and
whether the user chose to trace further or stop.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtrace_chains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "origin_paper_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "origin_sentence_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("paper_sentences.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("origin_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "backtrace_hops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chain_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("backtrace_chains.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("hop_index", sa.Integer(), nullable=False),
        sa.Column("to_openalex_id", sa.String(length=100), nullable=False),
        sa.Column("to_doi", sa.String(length=255), nullable=True),
        sa.Column("to_title", sa.Text(), nullable=False),
        sa.Column("to_authors_json", postgresql.JSONB(), nullable=True),
        sa.Column("to_year", sa.Integer(), nullable=True),
        sa.Column("to_venue", sa.String(length=500), nullable=True),
        sa.Column(
            "to_paper_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("quote_source", sa.String(length=20), nullable=True),
        sa.Column("relevance_note", sa.Text(), nullable=True),
        sa.Column("match_reasoning", sa.Text(), nullable=True),
        sa.Column(
            "resolution_status", sa.String(length=30), nullable=False, server_default="pending"
        ),
        sa.Column("user_decision", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("chain_id", "to_openalex_id", name="backtrace_hops_chain_target_uq"),
    )
    op.create_check_constraint(
        "backtrace_hops_quote_source_chk",
        "backtrace_hops",
        "quote_source IN ('abstract', 'full_text')",
    )
    op.create_check_constraint(
        "backtrace_hops_resolution_status_chk",
        "backtrace_hops",
        "resolution_status IN ('pending', 'ingesting', 'abstract_match', 'full_text_match', "
        "'no_match', 'awaiting_manual_import')",
    )
    op.create_check_constraint(
        "backtrace_hops_user_decision_chk",
        "backtrace_hops",
        "user_decision IN ('pending', 'continued', 'stopped_here')",
    )
    op.create_index("backtrace_hops_chain_idx", "backtrace_hops", ["chain_id", "hop_index"])


def downgrade() -> None:
    op.drop_index("backtrace_hops_chain_idx", table_name="backtrace_hops")
    op.drop_table("backtrace_hops")
    op.drop_table("backtrace_chains")
