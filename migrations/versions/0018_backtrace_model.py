"""Add backtrace_hops.model — persists which LLM actually generated a hop's
match reasoning/quote, mirroring the existing Synthesis.model/
Passage.checker_model convention, so the UI can attribute AI-generated text
to a real model rather than a hardcoded guess.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtrace_hops", sa.Column("model", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("backtrace_hops", "model")
