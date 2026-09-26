"""audit_findings: verdict, secondhand, confidence

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-15

Adds graded verdicts beside the pass/fail `supported` column, which stays until
the Audit panel switches over. Existing findings are backfilled only where the
graded verdict is certain: a pass becomes `supported`, and a fail recorded only
because the citation had no specific quote becomes `indeterminate`. Every other
fail stays NULL. After the fact there is no way to tell a misread from an
overreach, and guessing would plant wrong labels in the data.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

# The start of the rationale synthesize/audit.py records for a citation with
# no quote, frozen here as it was when this migration was written.
_NO_QUOTE_RATIONALE_PREFIX = "This citation has no specific verbatim evidence attached"


def upgrade() -> None:
    """Add the three columns, backfill the certain verdicts, then constrain."""
    op.add_column("audit_findings", sa.Column("verdict", sa.String(30)))
    op.add_column("audit_findings", sa.Column("secondhand", sa.Boolean))
    op.add_column("audit_findings", sa.Column("confidence", sa.Float))
    bind = op.get_bind()

    passed = bind.execute(
        sa.text("UPDATE audit_findings SET verdict = 'supported' WHERE supported")
    ).rowcount
    no_quote = bind.execute(
        sa.text(
            "UPDATE audit_findings SET verdict = 'indeterminate' "
            "WHERE NOT supported AND quote IS NULL AND rationale LIKE :prefix"
        ),
        {"prefix": _NO_QUOTE_RATIONALE_PREFIX + "%"},
    ).rowcount
    ungraded = bind.execute(
        sa.text("SELECT count(*) FROM audit_findings WHERE verdict IS NULL")
    ).scalar_one()

    op.create_check_constraint(
        "audit_findings_verdict_chk",
        "audit_findings",
        "verdict IS NULL OR verdict IN ('supported', 'misread', 'overreach', "
        "'indeterminate', 'differs_from_source')",
    )
    op.create_check_constraint(
        "audit_findings_confidence_chk",
        "audit_findings",
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
    )

    log.info("0026: %d passing findings -> supported", passed)
    log.info("0026: %d findings failed only for lack of a quote -> indeterminate", no_quote)
    log.info("0026: %d other findings left without a graded verdict", ungraded)


def downgrade() -> None:
    """Drop the columns; graded verdicts recorded since the upgrade are lost."""
    bind = op.get_bind()
    graded = bind.execute(
        sa.text("SELECT count(*) FROM audit_findings WHERE verdict IS NOT NULL")
    ).scalar_one()
    log.info("0026 downgrade: dropping verdict, secondhand, confidence; %d graded findings", graded)
    op.drop_constraint("audit_findings_confidence_chk", "audit_findings", type_="check")
    op.drop_constraint("audit_findings_verdict_chk", "audit_findings", type_="check")
    op.drop_column("audit_findings", "confidence")
    op.drop_column("audit_findings", "secondhand")
    op.drop_column("audit_findings", "verdict")
