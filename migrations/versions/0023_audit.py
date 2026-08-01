"""audit tables: audit_runs, audit_findings

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-01

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "compose_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compose_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("model", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')", name="audit_runs_status_chk"
        ),
    )
    op.create_index("audit_runs_project_idx", "audit_runs", ["project_id"])
    op.create_index(
        "audit_runs_compose_document_idx", "audit_runs", ["compose_document_id"]
    )

    op.create_table(
        "audit_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "audit_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column(
            "statement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("statements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sub_id", sa.String(20), nullable=False),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote", sa.Text),
        sa.Column("supported", sa.Boolean, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_kind IN ('paper','passage','dataset','statement')",
            name="audit_findings_target_kind_chk",
        ),
    )
    op.create_index(
        "audit_findings_run_ord_idx", "audit_findings", ["audit_run_id", "ord"]
    )


def downgrade() -> None:
    op.drop_table("audit_findings")
    op.drop_table("audit_runs")
