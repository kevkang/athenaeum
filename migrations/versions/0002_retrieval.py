"""retrieval: tsvector generated column + GIN index on paper_sentences

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated stored column so Postgres keeps it up-to-date automatically.
    # Using STORED (not VIRTUAL) so the GIN index can be built over it.
    op.execute(
        """
        ALTER TABLE paper_sentences
            ADD COLUMN text_search tsvector
            GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX paper_sentences_fts_gin_idx
            ON paper_sentences USING gin(text_search)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS paper_sentences_fts_gin_idx")
    op.execute("ALTER TABLE paper_sentences DROP COLUMN IF EXISTS text_search")
