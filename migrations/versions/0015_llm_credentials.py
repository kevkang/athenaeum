"""Add llm_credentials table for per-user "bring your own" LLM API keys, so a
user can plug in their own Claude API key which then takes precedence over
the server's global ANTHROPIC_API_KEY for their own Find/summarize requests.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-14

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_credentials",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("provider", sa.Text(), nullable=False, server_default="anthropic"),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("provider IN ('anthropic')", name="llm_credentials_provider_chk"),
    )


def downgrade() -> None:
    op.drop_table("llm_credentials")
