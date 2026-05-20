"""initial schema: users, projects, papers, sections, sentences

Revision ID: 0001
Revises:
Create Date: 2026-05-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("orcid", sa.String(19), nullable=False, unique=True),
        sa.Column("email", sa.String(320)),
        sa.Column("display_name", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("license", sa.String(50), nullable=False, server_default="CC-BY-4.0"),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "project_members",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'viewer', 'member')",
            name="project_members_role_chk",
        ),
    )

    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("doi", sa.String(255), unique=True),
        sa.Column("openalex_id", sa.String(50), unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("authors_json", postgresql.JSONB),
        sa.Column("venue", sa.String(500)),
        sa.Column("year", sa.Integer),
        sa.Column("abstract", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="ghost"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('full_text', 'ghost', 'failed')", name="papers_status_chk"
        ),
    )

    op.create_table(
        "paper_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("ord", sa.Integer, nullable=False),
        sa.Column("char_start", sa.BigInteger, nullable=False),
        sa.Column("char_end", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("paper_id", "ord", name="paper_sections_paper_ord_uq"),
    )

    # paper_sentences: written as raw SQL because the VECTOR type and HNSW
    # index aren't expressed cleanly via SQLAlchemy core ops in Alembic.
    op.execute(
        """
        CREATE TABLE paper_sentences (
            id UUID PRIMARY KEY,
            paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            section_id UUID NOT NULL REFERENCES paper_sections(id) ON DELETE CASCADE,
            ord INTEGER NOT NULL,
            text TEXT NOT NULL,
            char_start BIGINT NOT NULL,
            char_end BIGINT NOT NULL,
            embedding VECTOR(768),
            CONSTRAINT paper_sentences_paper_ord_uq UNIQUE (paper_id, ord)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX paper_sentences_embedding_hnsw_idx
            ON paper_sentences USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )

    op.create_table(
        "project_papers",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "paper_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "added_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("project_papers")
    op.execute("DROP INDEX IF EXISTS paper_sentences_embedding_hnsw_idx")
    op.execute("DROP TABLE IF EXISTS paper_sentences")
    op.drop_table("paper_sections")
    op.drop_table("papers")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
