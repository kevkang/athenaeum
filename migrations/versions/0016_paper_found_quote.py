"""Add papers.found_quote/found_relevance_note/found_corpus, so a verbatim
quote surfaced by a "Find" search can be persisted and highlighted in the
abstract block once the paper is imported. Also add passages.rationale,
capturing the extraction step's already-generated-but-previously-discarded
relevance rationale, so Project/Global Find cards have real "why relevant"
text to show (matching the same field Zotero/OpenAlex Find already produces).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("found_quote", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("found_relevance_note", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("found_corpus", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "papers_found_corpus_chk",
        "papers",
        "found_corpus IN ('zotero_sync', 'zotero_local', 'openalex')",
    )
    op.add_column("passages", sa.Column("rationale", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("passages", "rationale")
    op.drop_constraint("papers_found_corpus_chk", "papers", type_="check")
    op.drop_column("papers", "found_corpus")
    op.drop_column("papers", "found_relevance_note")
    op.drop_column("papers", "found_quote")
