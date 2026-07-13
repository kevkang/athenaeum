"""Rename bricks to statements; replace brick_builds_on + tension_edges with a
single typed statement_edges relation (relates_to / supports / tension).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── bricks -> statements ────────────────────────────────────────────────
    op.rename_table("bricks", "statements")
    op.drop_constraint("bricks_status_chk", "statements", type_="check")
    op.create_check_constraint(
        "statements_status_chk",
        "statements",
        "status IN ('draft','cemented','open','resolved','wontfix')",
    )

    # ── brick_rests_on -> statement_evidence ───────────────────────────────
    op.rename_table("brick_rests_on", "statement_evidence")
    op.alter_column("statement_evidence", "brick_id", new_column_name="statement_id")
    op.execute("UPDATE statement_evidence SET target_kind = 'statement' WHERE target_kind = 'brick'")
    op.drop_constraint("brick_rests_on_target_kind_chk", "statement_evidence", type_="check")
    op.create_check_constraint(
        "statement_evidence_target_kind_chk",
        "statement_evidence",
        "target_kind IN ('paper','passage','dataset','statement')",
    )

    # ── replace brick_builds_on + tension_edges with one typed edge table ──
    op.drop_table("brick_builds_on")
    op.drop_table("tension_edges")

    op.create_table(
        "statement_edges",
        sa.Column(
            "from_statement_id", UUID(as_uuid=True),
            sa.ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "to_statement_id", UUID(as_uuid=True),
            sa.ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("relation", sa.String(20), nullable=False),
        sa.Column(
            "created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "relation IN ('relates_to','supports','tension')", name="statement_edges_relation_chk"
        ),
        sa.CheckConstraint(
            "from_statement_id != to_statement_id", name="statement_edges_no_self_chk"
        ),
    )

    # ── annotations: 'brick' target kind -> 'statement' ────────────────────
    op.execute("UPDATE annotations SET target_kind = 'statement' WHERE target_kind = 'brick'")
    op.drop_constraint("annotations_target_kind_chk", "annotations", type_="check")
    op.create_check_constraint(
        "annotations_target_kind_chk",
        "annotations",
        "target_kind IN ('paper','passage','statement','sentence','section')",
    )


def downgrade() -> None:
    op.drop_constraint("annotations_target_kind_chk", "annotations", type_="check")
    op.create_check_constraint(
        "annotations_target_kind_chk",
        "annotations",
        "target_kind IN ('paper','passage','brick','sentence','section')",
    )
    op.execute("UPDATE annotations SET target_kind = 'brick' WHERE target_kind = 'statement'")

    op.drop_table("statement_edges")

    op.create_table(
        "tension_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("from_kind", sa.String(20), nullable=False),
        sa.Column("from_id", UUID(as_uuid=True), nullable=False),
        sa.Column("to_kind", sa.String(20), nullable=False),
        sa.Column("to_id", UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("evidence_passage_id", UUID(as_uuid=True), sa.ForeignKey("passages.id")),
    )
    op.create_table(
        "brick_builds_on",
        sa.Column(
            "brick_id", UUID(as_uuid=True),
            sa.ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "parent_brick_id", UUID(as_uuid=True),
            sa.ForeignKey("statements.id", ondelete="RESTRICT"), primary_key=True,
        ),
        sa.Column("ord", sa.Integer, nullable=False, server_default="0"),
    )

    op.execute("UPDATE statement_evidence SET target_kind = 'brick' WHERE target_kind = 'statement'")
    op.drop_constraint("statement_evidence_target_kind_chk", "statement_evidence", type_="check")
    op.create_check_constraint(
        "brick_rests_on_target_kind_chk",
        "statement_evidence",
        "target_kind IN ('paper','passage','dataset','brick')",
    )
    op.alter_column("statement_evidence", "statement_id", new_column_name="brick_id")
    op.rename_table("statement_evidence", "brick_rests_on")

    op.drop_constraint("statements_status_chk", "statements", type_="check")
    op.create_check_constraint(
        "bricks_status_chk",
        "statements",
        "status IN ('draft','cemented','open','resolved','wontfix')",
    )
    op.rename_table("statements", "bricks")
