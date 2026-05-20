"""bricks, annotations, questions, evaluations, provenance_log, citation/tension edges

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("storage_uri", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('figure','table','synthesis','raw')", name="datasets_kind_chk"
        ),
    )

    op.create_table(
        "bricks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("assertion", sa.Text, nullable=False),
        sa.Column("cemented_at", sa.DateTime(timezone=True)),
        sa.Column(
            "drafted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "built_from_synthesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("syntheses.id"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','cemented')", name="bricks_status_chk"
        ),
    )
    op.create_index("bricks_project_idx", "bricks", ["project_id"])

    op.create_table(
        "brick_rests_on",
        sa.Column(
            "brick_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bricks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_kind", sa.String(20), nullable=False, primary_key=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="evidence"),
        sa.CheckConstraint(
            "target_kind IN ('paper','passage','dataset','brick')",
            name="brick_rests_on_target_kind_chk",
        ),
    )

    op.create_table(
        "brick_builds_on",
        sa.Column(
            "brick_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bricks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "parent_brick_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bricks.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("ord", sa.Integer, nullable=False, server_default="0"),
    )

    op.create_table(
        "annotations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("target_kind", sa.String(20), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_kind IN ('paper','passage','brick','sentence','section')",
            name="annotations_target_kind_chk",
        ),
    )
    op.create_index("annotations_target_idx", "annotations", ["target_kind", "target_id"])

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("attached_to", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('open','resolved','wontfix')", name="questions_status_chk"
        ),
    )

    op.create_table(
        "evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "synthesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("syntheses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "passage_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("passages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("stance", sa.String(20), nullable=False),
        sa.Column("body", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("synthesis_id", "passage_id", "user_id", name="evaluations_uq"),
        sa.CheckConstraint(
            "stance IN ('agree','partial','disagree')", name="evaluations_stance_chk"
        ),
    )

    op.create_table(
        "provenance_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("payload_json", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "provenance_log_project_time_idx", "provenance_log", ["project_id", "created_at"]
    )

    op.create_table(
        "citation_edges",
        sa.Column(
            "from_paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "to_paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source", sa.String(20), nullable=False, server_default="openalex"),
        sa.CheckConstraint(
            "source IN ('openalex','extracted')", name="citation_edges_source_chk"
        ),
    )

    op.create_table(
        "tension_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_kind", sa.String(20), nullable=False),
        sa.Column("from_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_kind", sa.String(20), nullable=False),
        sa.Column("to_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column(
            "evidence_passage_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("passages.id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("tension_edges")
    op.drop_table("citation_edges")
    op.drop_table("provenance_log")
    op.drop_table("evaluations")
    op.drop_table("questions")
    op.drop_table("annotations")
    op.drop_table("brick_builds_on")
    op.drop_table("brick_rests_on")
    op.drop_table("bricks")
    op.drop_table("datasets")
