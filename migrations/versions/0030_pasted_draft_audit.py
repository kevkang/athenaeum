"""audit_runs: kind/draft_text; audit_draft_citations; findings anchored either way

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-26

Makes room for the Audit tab's pasted-draft runs alongside the existing
compose-document ones (decision D-N9).

Three shape changes:

1. `audit_runs.kind` discriminates the trigger, and `compose_document_id`
   becomes nullable because a pasted draft has no document. Every existing row
   is a compose-document run by construction — that was the only trigger that
   existed — so the backfill is certain rather than a guess.
2. `audit_draft_citations` holds one row per citation found in a draft: the
   claim it was attached to, what it resolved to, and what text the claim was
   checked against. It is a table rather than columns on a finding because a
   citation that could not be resolved never produces a finding and still has
   to be shown; otherwise "not checked" would be invisible next to "checked
   and fine".
3. `audit_findings` gains `draft_citation_id`, and `statement_id` /
   `target_kind` / `target_id` become nullable, with a CHECK making a finding
   anchor to exactly one of the two. The nullability is widened only for rows
   that do not exist yet: the CHECK still requires all three on any finding
   that names a statement, so no existing row loses a guarantee.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()

    # ── 1. audit_runs ────────────────────────────────────────────────────────
    op.add_column("audit_runs", sa.Column("kind", sa.String(30)))
    op.add_column("audit_runs", sa.Column("draft_text", sa.Text))

    backfilled = bind.execute(
        sa.text("UPDATE audit_runs SET kind = 'compose_document' WHERE kind IS NULL")
    ).rowcount
    op.alter_column("audit_runs", "kind", nullable=False)
    op.alter_column("audit_runs", "compose_document_id", nullable=True)

    op.create_check_constraint(
        "audit_runs_kind_chk", "audit_runs", "kind IN ('compose_document', 'pasted_draft')"
    )
    op.create_check_constraint(
        "audit_runs_kind_payload_chk",
        "audit_runs",
        "(kind = 'compose_document' AND compose_document_id IS NOT NULL "
        "AND draft_text IS NULL) OR "
        "(kind = 'pasted_draft' AND compose_document_id IS NULL AND draft_text IS NOT NULL)",
    )

    # ── 2. audit_draft_citations ─────────────────────────────────────────────
    op.create_table(
        "audit_draft_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "audit_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("claim_text", sa.Text, nullable=False),
        sa.Column("citation_text", sa.Text, nullable=False),
        sa.Column("resolution", sa.String(30), nullable=False),
        sa.Column("openalex_id", sa.String(50)),
        sa.Column("doi", sa.String(255)),
        sa.Column("matched_title", sa.Text),
        sa.Column("matched_year", sa.Integer),
        sa.Column("matched_authors_json", postgresql.JSONB),
        sa.Column("candidates_json", postgresql.JSONB),
        sa.Column(
            "paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="SET NULL"),
        ),
        sa.Column("evidence_source", sa.String(30)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "resolution IN ('exact', 'needs_confirmation', 'confirmed', 'not_found')",
            name="audit_draft_citations_resolution_chk",
        ),
        sa.CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN "
            "('project_full_text', 'abstract', 'no_text')",
            name="audit_draft_citations_evidence_source_chk",
        ),
        sa.CheckConstraint(
            "(resolution = 'not_found' AND openalex_id IS NULL) OR (resolution <> 'not_found')",
            name="audit_draft_citations_not_found_chk",
        ),
        sa.UniqueConstraint("audit_run_id", "ord", name="audit_draft_citations_run_ord_uq"),
    )

    # ── 3. audit_findings ────────────────────────────────────────────────────
    op.add_column(
        "audit_findings",
        sa.Column(
            "draft_citation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_draft_citations.id", ondelete="CASCADE"),
        ),
    )
    op.alter_column("audit_findings", "statement_id", nullable=True)
    op.alter_column("audit_findings", "target_kind", nullable=True)
    op.alter_column("audit_findings", "target_id", nullable=True)

    op.drop_constraint("audit_findings_target_kind_chk", "audit_findings", type_="check")
    op.create_check_constraint(
        "audit_findings_target_kind_chk",
        "audit_findings",
        "target_kind IS NULL OR target_kind IN ('paper','passage','dataset','statement')",
    )
    op.create_check_constraint(
        "audit_findings_anchor_chk",
        "audit_findings",
        "(statement_id IS NOT NULL AND target_kind IS NOT NULL AND target_id IS NOT NULL "
        "AND draft_citation_id IS NULL) OR "
        "(draft_citation_id IS NOT NULL AND statement_id IS NULL "
        "AND target_kind IS NULL AND target_id IS NULL)",
    )

    findings = bind.execute(sa.text("SELECT count(*) FROM audit_findings")).scalar_one()
    log.info("0030: %d audit runs backfilled to kind='compose_document'", backfilled)
    log.info("0030: %d existing findings keep their statement anchor unchanged", findings)


def downgrade() -> None:
    """Reverse all three changes. Any pasted-draft run is deleted: there is no
    compose document to point it at, so it cannot be represented in the old
    shape at all. Its findings and citations go with it via ON DELETE CASCADE."""
    bind = op.get_bind()
    draft_runs = bind.execute(
        sa.text("SELECT count(*) FROM audit_runs WHERE kind = 'pasted_draft'")
    ).scalar_one()
    log.info("0030 downgrade: deleting %d pasted-draft audit runs and their findings", draft_runs)

    op.drop_constraint("audit_findings_anchor_chk", "audit_findings", type_="check")
    bind.execute(sa.text("DELETE FROM audit_runs WHERE kind = 'pasted_draft'"))

    op.drop_column("audit_findings", "draft_citation_id")
    op.drop_table("audit_draft_citations")

    op.drop_constraint("audit_findings_target_kind_chk", "audit_findings", type_="check")
    op.create_check_constraint(
        "audit_findings_target_kind_chk",
        "audit_findings",
        "target_kind IN ('paper','passage','dataset','statement')",
    )
    op.alter_column("audit_findings", "statement_id", nullable=False)
    op.alter_column("audit_findings", "target_kind", nullable=False)
    op.alter_column("audit_findings", "target_id", nullable=False)

    op.drop_constraint("audit_runs_kind_payload_chk", "audit_runs", type_="check")
    op.drop_constraint("audit_runs_kind_chk", "audit_runs", type_="check")
    op.alter_column("audit_runs", "compose_document_id", nullable=False)
    op.drop_column("audit_runs", "draft_text")
    op.drop_column("audit_runs", "kind")
