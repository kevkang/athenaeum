"""Nucleate: nuclei, corpus nodes, lineage, reviews, evidence, expansion runs

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-27

The data model the Nucleate handoff says "must be correct before the first node
is written". Decisions behind it are in NUCLEATE_PLAN.md (D-N11..D-N14); the
vocabulary is in domain/nucleate.py. In brief:

- A paper is one `corpus_nodes` row per project, however many nuclei surface it.
- `lineage_edges` record how a node joined a nucleus. Membership of a nucleus
  *is* having one; there is no separate membership list.
- `node_reviews` hold the user's judgement per (node, nucleus). Each names the
  lineage edge that introduced it through a composite foreign key, so the
  database guarantees no review exists without lineage in the same nucleus
  pointing at the same node.
- `node_evidence` quotes always carry a TextQuoteSelector (NOT NULL): evidence
  is verified verbatim before it is stored, so it is always anchorable.
- `nucleus_prompts` are the method record; `nucleus_expansions` the run record
  for each, metering tokens; `nucleus_expansion_events` the persisted stream.
- `audit_runs` gains a third kind, 'nucleate_node', tied to the expansion that
  triggered it, and `audit_findings` a third anchor, the node review (D-N13).

Every new table is empty on creation; the only existing rows touched are none —
the audit changes widen constraints and add nullable columns. The counts logged
confirm that.

Dry run: `alembic -x dry_run=true upgrade head` logs the counts and rolls back.
"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_UUID = postgresql.UUID(as_uuid=True)


def _pk() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "nuclei",
        _pk(),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _created_at(),
    )

    op.create_table(
        "corpus_nodes",
        _pk(),
        sa.Column("project_id", _UUID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("paper_id", _UUID, sa.ForeignKey("papers.id", ondelete="CASCADE"),
                  nullable=False),
        _created_at(),
        sa.UniqueConstraint("project_id", "paper_id", name="corpus_nodes_project_paper_uq"),
    )

    op.create_table(
        "nucleus_prompts",
        _pk(),
        sa.Column("nucleus_id", _UUID, sa.ForeignKey("nuclei.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("anchor_node_ids", postgresql.ARRAY(_UUID), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        _created_at(),
    )

    op.create_table(
        "nucleus_expansions",
        _pk(),
        sa.Column("nucleus_id", _UUID, sa.ForeignKey("nuclei.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("prompt_id", _UUID, sa.ForeignKey("nucleus_prompts.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("budget", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(30)),
        sa.Column("reply_text", sa.Text),
        sa.Column("model", sa.String(100)),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('running', 'done', 'failed', 'cancelled')",
                           name="nucleus_expansions_status_chk"),
        sa.CheckConstraint("budget BETWEEN 1 AND 50", name="nucleus_expansions_budget_chk"),
        sa.CheckConstraint("input_tokens >= 0 AND output_tokens >= 0",
                           name="nucleus_expansions_tokens_chk"),
    )

    op.create_table(
        "nucleus_expansion_events",
        _pk(),
        sa.Column("expansion_id", _UUID,
                  sa.ForeignKey("nucleus_expansions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("node_id", _UUID, sa.ForeignKey("corpus_nodes.id", ondelete="SET NULL")),
        sa.Column("counts", postgresql.JSONB),
        sa.Column("message", sa.Text),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("expansion_id", "ord", name="nucleus_expansion_events_ord_uq"),
    )

    op.create_table(
        "lineage_edges",
        _pk(),
        sa.Column("nucleus_id", _UUID, sa.ForeignKey("nuclei.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("from_kind", sa.String(10), nullable=False),
        sa.Column("from_node_id", _UUID, sa.ForeignKey("corpus_nodes.id", ondelete="CASCADE")),
        sa.Column("to_node_id", _UUID, sa.ForeignKey("corpus_nodes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("prompt_id", _UUID, sa.ForeignKey("nucleus_prompts.id", ondelete="SET NULL")),
        sa.Column("retrieval_source", sa.String(20)),
        _created_at(),
        sa.CheckConstraint("from_kind IN ('nucleus', 'node')", name="lineage_edges_from_kind_chk"),
        sa.CheckConstraint(
            "(from_kind = 'nucleus' AND from_node_id IS NULL) OR "
            "(from_kind = 'node' AND from_node_id IS NOT NULL AND from_node_id <> to_node_id)",
            name="lineage_edges_from_chk",
        ),
        sa.CheckConstraint(
            "retrieval_source IS NULL OR retrieval_source IN ('library', 'openalex', 'web')",
            name="lineage_edges_retrieval_source_chk",
        ),
        sa.CheckConstraint("(prompt_id IS NULL) = (retrieval_source IS NULL)",
                           name="lineage_edges_retrieved_or_added_chk"),
        sa.UniqueConstraint("id", "nucleus_id", "to_node_id", name="lineage_edges_identity_uq"),
    )
    op.create_index("lineage_edges_membership_idx", "lineage_edges", ["nucleus_id", "to_node_id"])

    op.create_table(
        "node_reviews",
        _pk(),
        sa.Column("nucleus_id", _UUID, sa.ForeignKey("nuclei.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("node_id", _UUID, sa.ForeignKey("corpus_nodes.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("introduced_by_edge_id", _UUID, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="frontier"),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("relevance_text", sa.Text),
        sa.Column("relevance_provenance", sa.String(30)),
        sa.Column("selection_reasoning", sa.Text),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("nucleus_id", "node_id", name="node_reviews_nucleus_node_uq"),
        sa.ForeignKeyConstraint(
            ["introduced_by_edge_id", "nucleus_id", "node_id"],
            ["lineage_edges.id", "lineage_edges.nucleus_id", "lineage_edges.to_node_id"],
            name="node_reviews_introduced_by_edge_fk",
        ),
        sa.CheckConstraint("status IN ('frontier', 'accepted', 'rejected', 'pinned')",
                           name="node_reviews_status_chk"),
        sa.CheckConstraint("rejection_reason IS NULL OR status = 'rejected'",
                           name="node_reviews_rejection_reason_chk"),
        sa.CheckConstraint(
            "relevance_provenance IS NULL OR relevance_provenance IN "
            "('ai_proposed', 'ai_proposed_accepted', 'ai_proposed_corrected', 'user_set')",
            name="node_reviews_relevance_provenance_chk",
        ),
        sa.CheckConstraint("(relevance_text IS NULL) = (relevance_provenance IS NULL)",
                           name="node_reviews_relevance_pair_chk"),
    )

    op.create_table(
        "node_evidence",
        _pk(),
        sa.Column("review_id", _UUID, sa.ForeignKey("node_reviews.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("verbatim_text", sa.Text, nullable=False),
        sa.Column("text_quote_selector", postgresql.JSONB, nullable=False),
        sa.Column("passage_id", _UUID, sa.ForeignKey("passages.id", ondelete="SET NULL")),
        _created_at(),
        sa.UniqueConstraint("review_id", "ord", name="node_evidence_review_ord_uq"),
        sa.CheckConstraint("source IN ('full_text', 'abstract')", name="node_evidence_source_chk"),
        sa.CheckConstraint("text_quote_selector->>'type' = 'TextQuoteSelector'",
                           name="node_evidence_text_quote_selector_chk"),
    )

    # ── audit_runs: a third kind, tied to its expansion (D-N13) ──────────────
    op.add_column(
        "audit_runs",
        sa.Column("expansion_id", _UUID,
                  sa.ForeignKey("nucleus_expansions.id", ondelete="CASCADE")),
    )
    op.drop_constraint("audit_runs_kind_payload_chk", "audit_runs", type_="check")
    op.drop_constraint("audit_runs_kind_chk", "audit_runs", type_="check")
    op.create_check_constraint(
        "audit_runs_kind_chk", "audit_runs",
        "kind IN ('compose_document', 'pasted_draft', 'nucleate_node')",
    )
    op.create_check_constraint(
        "audit_runs_kind_payload_chk", "audit_runs",
        "(kind = 'compose_document' AND compose_document_id IS NOT NULL "
        "AND draft_text IS NULL AND expansion_id IS NULL) OR "
        "(kind = 'pasted_draft' AND compose_document_id IS NULL "
        "AND draft_text IS NOT NULL AND expansion_id IS NULL) OR "
        "(kind = 'nucleate_node' AND compose_document_id IS NULL "
        "AND draft_text IS NULL AND expansion_id IS NOT NULL)",
    )

    # ── audit_findings: a third anchor, the node review ──────────────────────
    op.add_column(
        "audit_findings",
        sa.Column("node_review_id", _UUID, sa.ForeignKey("node_reviews.id", ondelete="CASCADE")),
    )
    op.drop_constraint("audit_findings_anchor_chk", "audit_findings", type_="check")
    op.create_check_constraint(
        "audit_findings_anchor_chk", "audit_findings",
        "(statement_id IS NOT NULL AND target_kind IS NOT NULL AND target_id IS NOT NULL "
        "AND draft_citation_id IS NULL AND node_review_id IS NULL) OR "
        "(draft_citation_id IS NOT NULL AND statement_id IS NULL "
        "AND target_kind IS NULL AND target_id IS NULL AND node_review_id IS NULL) OR "
        "(node_review_id IS NOT NULL AND statement_id IS NULL AND draft_citation_id IS NULL "
        "AND target_kind IS NULL AND target_id IS NULL)",
    )

    bind = op.get_bind()
    runs = bind.execute(sa.text("SELECT count(*) FROM audit_runs")).scalar_one()
    findings = bind.execute(sa.text("SELECT count(*) FROM audit_findings")).scalar_one()
    log.info("0032: 8 Nucleate tables created, all empty")
    log.info("0032: %d existing audit runs and %d findings still satisfy the widened "
             "constraints unchanged", runs, findings)


def downgrade() -> None:
    """Drop every Nucleate table and narrow the audit tables back. Nucleate
    audit runs cannot be represented in the old shape, so they are deleted
    first; their findings go with them by cascade."""
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in ("nuclei", "corpus_nodes", "node_reviews", "node_evidence",
                      "nucleus_expansions")
    }
    nucleate_runs = bind.execute(
        sa.text("SELECT count(*) FROM audit_runs WHERE kind = 'nucleate_node'")
    ).scalar_one()
    log.info("0032 downgrade: discarding %s and %d nucleate audit runs", counts, nucleate_runs)

    op.drop_constraint("audit_findings_anchor_chk", "audit_findings", type_="check")
    op.drop_constraint("audit_runs_kind_payload_chk", "audit_runs", type_="check")
    op.drop_constraint("audit_runs_kind_chk", "audit_runs", type_="check")
    bind.execute(sa.text("DELETE FROM audit_runs WHERE kind = 'nucleate_node'"))

    op.drop_column("audit_findings", "node_review_id")
    op.create_check_constraint(
        "audit_findings_anchor_chk", "audit_findings",
        "(statement_id IS NOT NULL AND target_kind IS NOT NULL AND target_id IS NOT NULL "
        "AND draft_citation_id IS NULL) OR "
        "(draft_citation_id IS NOT NULL AND statement_id IS NULL "
        "AND target_kind IS NULL AND target_id IS NULL)",
    )
    op.drop_column("audit_runs", "expansion_id")
    op.create_check_constraint(
        "audit_runs_kind_chk", "audit_runs", "kind IN ('compose_document', 'pasted_draft')"
    )
    op.create_check_constraint(
        "audit_runs_kind_payload_chk", "audit_runs",
        "(kind = 'compose_document' AND compose_document_id IS NOT NULL "
        "AND draft_text IS NULL) OR "
        "(kind = 'pasted_draft' AND compose_document_id IS NULL AND draft_text IS NOT NULL)",
    )

    for table in ("node_evidence", "node_reviews"):
        op.drop_table(table)
    op.drop_index("lineage_edges_membership_idx", table_name="lineage_edges")
    for table in ("lineage_edges", "nucleus_expansion_events", "nucleus_expansions",
                  "nucleus_prompts", "corpus_nodes", "nuclei"):
        op.drop_table(table)
