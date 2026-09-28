"""papers: oa_pdf_url, oa_checked_at (the Unpaywall check)

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-27

Records, per work, whether a legal open-access PDF was found and when anyone
looked (NUCLEATE_PLAN.md D-N15). Nucleate uses it two ways: an expansion
fetches the PDF in the background when there is one, and the needs-full-text
queue must show "the DOI and an Unpaywall check with each request" (handoff
Part V).

Two columns rather than one because three states have to stay apart: never
checked (both NULL), checked and found (URL set), and checked with no legal
open-access copy (oa_checked_at set, URL NULL). The last is what tells a
researcher the paper genuinely has to come from them.

On the work, not the project: open-access status belongs to the paper itself.

Pure column addition; no existing row changes.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column("papers", sa.Column("oa_pdf_url", sa.Text))
    op.add_column("papers", sa.Column("oa_checked_at", sa.DateTime(timezone=True)))
    papers = op.get_bind().execute(sa.text("SELECT count(*) FROM papers")).scalar_one()
    log.info("0034: %d papers, all 'never checked' until an expansion looks", papers)


def downgrade() -> None:
    """Drop both columns; every recorded open-access check is lost."""
    checked = op.get_bind().execute(
        sa.text("SELECT count(*) FROM papers WHERE oa_checked_at IS NOT NULL")
    ).scalar_one()
    log.info("0034 downgrade: discarding %d recorded open-access checks", checked)
    op.drop_column("papers", "oa_checked_at")
    op.drop_column("papers", "oa_pdf_url")
