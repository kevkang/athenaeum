from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    orcid: Mapped[str] = mapped_column(String(19), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    projects: Mapped[list[Project]] = relationship(back_populates="owner")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str] = mapped_column(String(50), nullable=False, default="CC-BY-4.0")
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    # passive_deletes=True: without it, SQLAlchemy's default cascade tries
    # to *disassociate* members from a deleted project by nulling out
    # project_id — illegal here since it's part of ProjectMember's
    # composite primary key, not just a plain FK. This tells the ORM to
    # leave cascading to the DB's own ON DELETE CASCADE (see migration
    # 0022) instead, the same pairing already used for e.g.
    # BacktraceChain.hops.
    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", passive_deletes=True
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'editor', 'viewer', 'member')",
            name="project_members_role_chk",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="members")


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('full_text', 'ghost', 'failed')", name="papers_status_chk"
        ),
        CheckConstraint(
            "zotero_source IN ('sync', 'local')", name="papers_zotero_source_chk"
        ),
        CheckConstraint(
            "found_corpus IN ('zotero_sync', 'zotero_local', 'openalex')",
            name="papers_found_corpus_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    doi: Mapped[str | None] = mapped_column(String(255), unique=True)
    openalex_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    venue: Mapped[str | None] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column(Integer)
    abstract: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ghost")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    zotero_item_key: Mapped[str | None] = mapped_column(String(50), unique=True)
    zotero_imported_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Distinguishes which Zotero path imported this paper — 'sync' retries
    # need the importer's stored Web API credentials, 'local' retries just
    # re-read the bind-mounted data directory. NULL for non-Zotero papers.
    zotero_source: Mapped[str | None] = mapped_column(String(10))
    # Verbatim quote (and why it's relevant) surfaced by a "Find" search at
    # import time — comes from the abstract, not the ingested body text, so
    # it's highlighted in the abstract block, not matched against sentences.
    # Single-slot: re-finding via a different corpus later overwrites these.
    found_quote: Mapped[str | None] = mapped_column(Text)
    found_relevance_note: Mapped[str | None] = mapped_column(Text)
    found_corpus: Mapped[str | None] = mapped_column(String(20))


class PaperSection(Base):
    __tablename__ = "paper_sections"
    __table_args__ = (
        UniqueConstraint("paper_id", "ord", name="paper_sections_paper_ord_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    char_end: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PaperSentence(Base):
    __tablename__ = "paper_sentences"
    __table_args__ = (
        UniqueConstraint("paper_id", "ord", name="paper_sentences_paper_ord_uq"),
        Index(
            "paper_sentences_embedding_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    char_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    text_search: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', text)", persisted=True),
        deferred=True,
    )


class Passage(Base):
    __tablename__ = "passages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    sentence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checker_model: Mapped[str | None] = mapped_column(String(100))
    # Why the extraction step selected this passage as evidence — generated
    # by the same LLM call that produces sentence_ids/verbatim_text.
    rationale: Mapped[str | None] = mapped_column(Text)


class Synthesis(Base):
    __tablename__ = "syntheses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'done', 'failed')", name="syntheses_status_chk"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    synthesis_text: Mapped[str | None] = mapped_column(Text)
    query_expansion_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    retrieval_corpus: Mapped[str] = mapped_column(
        String(20), nullable=False, default="project"
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    checker_model: Mapped[str | None] = mapped_column(String(100))
    verification_rate: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    steps: Mapped[list[SynthesisStep]] = relationship(
        back_populates="synthesis", order_by="SynthesisStep.ord", passive_deletes=True
    )


class SynthesisEvidence(Base):
    __tablename__ = "synthesis_evidence"
    __table_args__ = (
        CheckConstraint(
            "role IN ('theory','opposing','framework','stakes','method','data')",
            name="synthesis_evidence_role_chk",
        ),
    )

    synthesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syntheses.id", ondelete="CASCADE"), primary_key=True
    )
    passage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("passages.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)


class SynthesisStep(Base):
    __tablename__ = "synthesis_steps"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('query_interp','retrieval','extract','signal','verify')",
            name="synthesis_steps_kind_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    synthesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syntheses.id", ondelete="CASCADE"), nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    synthesis: Mapped[Synthesis] = relationship(back_populates="steps")


class ProjectPaper(Base):
    __tablename__ = "project_papers"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )


# ── Phase 4 ────────────────────────────────────────────────────────────────────

class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("kind IN ('figure','table','synthesis','raw')", name="datasets_kind_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','locked','open','resolved','wontfix')",
            name="statements_status_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    assertion: Mapped[str] = mapped_column(Text, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drafted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    built_from_synthesis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syntheses.id", ondelete="SET NULL")
    )

    evidence: Mapped[list[StatementEvidence]] = relationship(back_populates="statement")


class StatementEvidence(Base):
    __tablename__ = "statement_evidence"
    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('paper','passage','dataset','statement')",
            name="statement_evidence_target_kind_chk",
        ),
    )

    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True
    )
    target_kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="evidence")

    statement: Mapped[Statement] = relationship(back_populates="evidence")


class StatementEdge(Base):
    """A user-drawn relation between two statements in the graph:
    relates_to (neutral), supports (positive), or tension (negative)."""

    __tablename__ = "statement_edges"
    __table_args__ = (
        CheckConstraint(
            "relation IN ('relates_to','supports','tension')",
            name="statement_edges_relation_chk",
        ),
        CheckConstraint(
            "from_statement_id != to_statement_id", name="statement_edges_no_self_chk"
        ),
    )

    from_statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True
    )
    to_statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('paper','passage','statement','sentence','section')",
            name="annotations_target_kind_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    target_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )



class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("synthesis_id", "passage_id", "user_id", name="evaluations_uq"),
        CheckConstraint(
            "stance IN ('agree','partial','disagree')", name="evaluations_stance_chk"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    synthesis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syntheses.id", ondelete="CASCADE"), nullable=False
    )
    passage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("passages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    stance: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("project_id", "project_seq", name="events_project_seq_uq"),
        Index("events_entity_idx", "entity_kind", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    prev_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectEventSeq(Base):
    __tablename__ = "project_event_seq"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class CitationEdge(Base):
    __tablename__ = "citation_edges"
    __table_args__ = (
        CheckConstraint(
            "source IN ('openalex','extracted')", name="citation_edges_source_chk"
        ),
    )

    from_paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    to_paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="openalex")


class KnowledgeLink(Base):
    __tablename__ = "knowledge_links"
    __table_args__ = (
        CheckConstraint(
            "from_type IN ('paper','synthesis')",
            name="knowledge_links_from_type_chk",
        ),
        CheckConstraint(
            "to_type IN ('paper','synthesis')",
            name="knowledge_links_to_type_chk",
        ),
        CheckConstraint(
            "rel IN ('supports','contradicts','extends','cites','related')",
            name="knowledge_links_rel_chk",
        ),
        UniqueConstraint(
            "from_id", "from_type", "to_id", "to_type", "rel",
            name="knowledge_links_uq",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    from_type: Mapped[str] = mapped_column(String(20), nullable=False)
    from_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    to_type: Mapped[str] = mapped_column(String(20), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rel: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ZoteroCredentials(Base):
    __tablename__ = "zotero_credentials"
    __table_args__ = (
        CheckConstraint(
            "library_type IN ('user','group')", name="zotero_credentials_library_type_chk"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    zotero_user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    library_type: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LLMCredentials(Base):
    """Per-user "bring your own" LLM API key. When present, this key takes
    precedence over the server's global ANTHROPIC_API_KEY for that user's
    Find/summarize requests — see provider/resolver.py."""
    __tablename__ = "llm_credentials"
    __table_args__ = (
        CheckConstraint("provider IN ('anthropic')", name="llm_credentials_provider_chk"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="anthropic")
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BacktraceChain(Base):
    """A user-driven citation trail: starting from a claim in a paper being
    read, each hop (see BacktraceHop) identifies the paper's own OpenAlex
    reference that most plausibly supports the same claim, one hop at a time,
    until the user marks a hop as the true/primary source."""
    __tablename__ = "backtrace_chains"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    origin_paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    origin_sentence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_sentences.id", ondelete="SET NULL")
    )
    origin_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    hops: Mapped[list[BacktraceHop]] = relationship(
        back_populates="chain", order_by="BacktraceHop.hop_index", passive_deletes=True
    )


class BacktraceHop(Base):
    __tablename__ = "backtrace_hops"
    __table_args__ = (
        UniqueConstraint("chain_id", "to_openalex_id", name="backtrace_hops_chain_target_uq"),
        CheckConstraint(
            "quote_source IN ('abstract', 'full_text')", name="backtrace_hops_quote_source_chk"
        ),
        CheckConstraint(
            "resolution_status IN ('pending', 'ingesting', 'abstract_match', 'full_text_match', "
            "'no_match', 'awaiting_manual_import')",
            name="backtrace_hops_resolution_status_chk",
        ),
        CheckConstraint(
            "user_decision IN ('pending', 'continued', 'stopped_here')",
            name="backtrace_hops_user_decision_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    chain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtrace_chains.id", ondelete="CASCADE"), nullable=False
    )
    # NULL means this hop branches directly off the chain's origin paper.
    # Together with hop_index (now "depth from origin," not a unique
    # per-chain sequence number — several hops can share a depth as
    # siblings), this makes a chain's hops a tree rather than a line: a hop
    # can have any number of children, letting a user explore several of a
    # claim's citations as parallel branches instead of only ever
    # continuing from "the last hop."
    parent_hop_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtrace_hops.id", ondelete="CASCADE")
    )
    hop_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshotted target-paper metadata — the target usually isn't an ingested
    # Paper row yet (see to_paper_id), so identity/display data lives here.
    to_openalex_id: Mapped[str] = mapped_column(String(100), nullable=False)
    to_doi: Mapped[str | None] = mapped_column(String(255))
    to_title: Mapped[str] = mapped_column(Text, nullable=False)
    to_authors_json: Mapped[list[str] | None] = mapped_column(JSONB)
    to_year: Mapped[int | None] = mapped_column(Integer)
    to_venue: Mapped[str | None] = mapped_column(String(500))
    to_paper_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="SET NULL")
    )
    quote: Mapped[str | None] = mapped_column(Text)
    quote_source: Mapped[str | None] = mapped_column(String(20))
    relevance_note: Mapped[str | None] = mapped_column(Text)
    # The LLM's stated reasoning for picking this reference among the paper's
    # other citations — surfaced in the UI so the user can judge the guess,
    # since there's no ground truth linking a sentence to a specific reference.
    match_reasoning: Mapped[str | None] = mapped_column(Text)
    # Which model actually generated match_reasoning/quote/relevance_note —
    # persisted (not derived from current settings) so attribution stays
    # accurate even if the configured model changes later, mirroring
    # Synthesis.model / Passage.checker_model.
    model: Mapped[str | None] = mapped_column(String(100))
    resolution_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    user_decision: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chain: Mapped[BacktraceChain] = relationship(back_populates="hops")


class ZoteroLocalConfig(Base):
    """Singleton row (id is always 1) recording which subfolder of the
    broadly-mounted host home directory (see ZOTERO_LOCAL_HOST_MOUNT) is the
    operator's actual Zotero data directory. Server-wide, not per-user — see
    the reasoning in config.py for why local Zotero isn't a per-account
    concept the way the Web API connection is."""
    __tablename__ = "zotero_local_config"
    __table_args__ = (CheckConstraint("id = 1", name="zotero_local_config_singleton_chk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ComposeDocument(Base):
    """One per project — the long-form TipTap/ProseMirror draft. Headings
    (H1-H4) give it internal structure (Intro/Methods/Discussion) without
    being separate documents. `version` backs optimistic-concurrency checks
    on the autosave PUT; this is not event-logged (see events/applier.py
    docstring) — continuous free-text editing isn't a discrete transition
    the way Statement.status changes are, and logging every debounced
    autosave would flood /history."""
    __tablename__ = "compose_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Highlight(Base):
    """A personal 'maybe useful' bookmark in the reader pane — yellow,
    independent of the evidence-linking system (StatementEvidence). Mirrors
    Passage's shape (paper_id, sentence_ids array, verbatim_text) minus the
    verification/synthesis bookkeeping, which is meaningless for a bookmark
    the user made unilaterally while reading. Not event-logged, matching
    Annotation's precedent for personal auxiliary content."""
    __tablename__ = "highlights"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    sentence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
