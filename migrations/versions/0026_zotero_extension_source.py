"""Allow 'extension' as a papers.zotero_source value, for papers imported via
the Zotero Connector browser extension (path 4) — see
ZOTERO_EXTENSION_PLAN.md step 7.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-22

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("papers_zotero_source_chk", "papers", type_="check")
    op.create_check_constraint(
        "papers_zotero_source_chk",
        "papers",
        "zotero_source IN ('sync', 'local', 'extension')",
    )


def downgrade() -> None:
    op.drop_constraint("papers_zotero_source_chk", "papers", type_="check")
    op.create_check_constraint(
        "papers_zotero_source_chk",
        "papers",
        "zotero_source IN ('sync', 'local')",
    )
