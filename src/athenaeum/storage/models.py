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
    ForeignKeyConstraint,
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
    # The Unpaywall check (NUCLEATE_PLAN.md D-N15): the open-access PDF found for
    # this work, and when it was looked for. A property of the work, not of any
    # project. oa_checked_at set with oa_pdf_url NULL means "checked, no legal
    # open-access copy" — distinct from never checked (both NULL), and that
    # difference is what the needs-full-text queue has to show.
    oa_pdf_url: Mapped[str | None] = mapped_column(Text)
    oa_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    __table_args__ = (
        CheckConstraint(
            "source_check IN ('matches_source', 'differs_from_source', 'source_unavailable')",
            name="passages_source_check_chk",
        ),
        CheckConstraint(
            "source_check_corrected IS NULL OR source_check_corrected IN "
            "('matches_source', 'differs_from_source', 'source_unavailable')",
            name="passages_source_check_corrected_chk",
        ),
        CheckConstraint(
            "text_quote_selector IS NULL OR ("
            "jsonb_typeof(text_quote_selector) = 'object' "
            "AND text_quote_selector->>'type' IS NOT DISTINCT FROM 'TextQuoteSelector' "
            "AND text_quote_selector ? 'exact')",
            name="passages_text_quote_selector_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    sentence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Whether this quote's words were found in this server's own copy of the
    # paper (see domain/source_check.py). Every path that creates a passage
    # sets it explicitly; there is deliberately no default, so a new path that
    # forgets fails loudly instead of silently claiming a match.
    source_check: Mapped[str] = mapped_column(String(30), nullable=False)
    # The same quote re-checked by migration 0027 with the corrected
    # comparison, kept beside the original rather than overwriting it: the
    # first comparison had a bug that flagged genuine quotes, and the
    # difference between the two verdicts has to stay inspectable. NULL on
    # every passage the migration did not re-check.
    source_check_corrected: Mapped[str | None] = mapped_column(String(30))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checker_model: Mapped[str | None] = mapped_column(String(100))
    # Why the extraction step selected this passage as evidence — generated
    # by the same LLM call that produces sentence_ids/verbatim_text.
    rationale: Mapped[str | None] = mapped_column(Text)
    # W3C Web Annotation TextQuoteSelector — {type, exact, prefix, suffix} —
    # anchoring this quote in the paper's text by its words rather than by
    # sentence ids, which mean nothing outside this database. Built by
    # domain/source_check.text_quote_selector from this server's own copy, so it
    # is NULL whenever the quote is not found there (differs_from_source,
    # source_unavailable): a selector says where a quote is, and is never
    # invented for one that isn't.
    text_quote_selector: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))


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
    # Historical only, no longer written: the share of passages a checker judged
    # both "plausibly verbatim" and "relevant". The verbatim half measured
    # nothing for passages rebuilt from stored sentences.
    verification_rate: Mapped[float | None] = mapped_column(Float)
    # Share of passages an independent checker judged relevant to the claim.
    # None when nothing was measured (no passages, or the checker failed).
    relevance_rate: Mapped[float | None] = mapped_column(Float)
    # Set when the synthesis arrived in a .ath import instead of running on this
    # server. Its rates were measured elsewhere, so public aggregates skip it.
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    __table_args__ = (
        CheckConstraint(
            "text_quote_selector IS NULL OR ("
            "jsonb_typeof(text_quote_selector) = 'object' "
            "AND text_quote_selector->>'type' IS NOT DISTINCT FROM 'TextQuoteSelector' "
            "AND text_quote_selector ? 'exact')",
            name="highlights_text_quote_selector_chk",
        ),
    )

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
    # ── Zotero provenance, captured at import (see api/zotero_highlights.py) ──
    # All four are NULL for a highlight the user made in Athenaeum's own reader,
    # and for Zotero imports predating migration 0029.
    #
    # The annotation this highlight was imported from. Stored because it is the
    # only stable way to recognise the same annotation again: matching on
    # sentence span collides whenever two annotations land on one sentence, and
    # it is also what lets position be backfilled later for a row imported
    # while Zotero was unreachable.
    zotero_annotation_key: Mapped[str | None] = mapped_column(String(50))
    # Zotero's own position object, {"pageIndex": int, "rects": [[...]]}, kept
    # unreshaped so it can be handed back to a Zotero-compatible reader as-is.
    # Capturing it at import is not an optimization: an annotation edited or
    # deleted in Zotero afterwards takes its coordinates with it.
    zotero_position_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Zotero's document-order key, which sorts annotations within one PDF
    # without interpreting the position rectangles.
    zotero_sort_index: Mapped[str | None] = mapped_column(String(50))
    # The page as printed in the document ("iv", "S3", "1247"), which is not
    # position_json's zero-based pageIndex and cannot be derived from it.
    zotero_page_label: Mapped[str | None] = mapped_column(String(50))
    # W3C TextQuoteSelector for this highlight's words, same meaning as
    # Passage.text_quote_selector. Distinct from the Zotero position above:
    # that is page geometry in one PDF, this is the text, and survives a
    # different PDF or a re-extraction.
    text_quote_selector: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))


class AuditRun(Base):
    """One run of Audit mode over a compose document — for each statement's
    evidence, an independent checker model judges whether the verbatim quote
    actually supports the claim (see synthesize/audit.py, which reuses the
    same independent-checker pattern as Synthesis's verifier.py). Unlike
    Synthesis there's no separate step-log table: each AuditFinding row IS
    the progress unit, published over Redis as it's created and persisted
    at the same time — nothing ephemeral to separately replay."""
    __tablename__ = "audit_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'done', 'failed')", name="audit_runs_status_chk"),
        CheckConstraint(
            "kind IN ('compose_document', 'pasted_draft', 'nucleate_node')",
            name="audit_runs_kind_chk",
        ),
        # Each trigger carries its own payload and none may borrow another's: a
        # compose run points at a document, a draft run carries the text it was
        # handed, a Nucleate run points at the expansion that triggered it.
        CheckConstraint(
            "(kind = 'compose_document' AND compose_document_id IS NOT NULL "
            "AND draft_text IS NULL AND expansion_id IS NULL) OR "
            "(kind = 'pasted_draft' AND compose_document_id IS NULL "
            "AND draft_text IS NOT NULL AND expansion_id IS NULL) OR "
            "(kind = 'nucleate_node' AND compose_document_id IS NULL "
            "AND draft_text IS NULL AND expansion_id IS NOT NULL)",
            name="audit_runs_kind_payload_chk",
        ),
        CheckConstraint(
            "quotes_checked IS NULL OR quotes_checked >= 0",
            name="audit_runs_quotes_checked_chk",
        ),
        CheckConstraint(
            "malformed_entries IS NULL OR (malformed_entries >= 0 "
            "AND malformed_entries <= quotes_checked)",
            name="audit_runs_malformed_entries_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # Which trigger produced this run (domain/draft_audit.py). The handoff
    # requires the triggers stay distinguishable in the run records; this is
    # that distinction, and Nucleate's automatic audit becomes a third value
    # rather than a third table.
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="compose_document")
    # NULL for a pasted-draft run, which has no document behind it.
    compose_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compose_documents.id", ondelete="CASCADE")
    )
    # The text submitted to a pasted-draft run, kept so a finding can always be
    # read back against the words it was made about. NULL for compose runs.
    draft_text: Mapped[str | None] = mapped_column(Text)
    # The Nucleate expansion whose relevance statements this run audited
    # automatically (D-N13). NULL for the other two kinds.
    expansion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nucleus_expansions.id", ondelete="CASCADE")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # How much evidence this run put to the checker, and how many entries in
    # its responses could not be used (synthesize/audit.py). NULL, not 0, on
    # runs from before this was counted: they were never measured, and a 0
    # would claim a clean response rate nobody observed.
    quotes_checked: Mapped[int | None] = mapped_column(Integer)
    malformed_entries: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    findings: Mapped[list[AuditFinding]] = relationship(
        back_populates="audit_run", order_by="AuditFinding.ord", passive_deletes=True
    )


class AuditDraftCitation(Base):
    """One citation extracted from a pasted draft, with the claim it was
    attached to and whatever work it was matched to.

    This is the row a draft audit's finding hangs off, in place of the
    Statement a compose-document audit uses. It exists as its own table rather
    than as columns on AuditFinding because a citation outlives its finding: a
    citation that could not be resolved, or that resolved to something with no
    readable text, never produces a finding at all and still has to be shown to
    the user. Collapsing the two would make "not checked" indistinguishable
    from "checked and fine" by absence.

    The matched work is stored denormalised (openalex_id, doi, title, year)
    rather than as a Paper row: resolving a citation should not silently add
    papers to the user's project, least of all for someone auditing a
    manuscript without a corpus. `paper_id` is set only when the project
    already happened to hold that paper.
    """

    __tablename__ = "audit_draft_citations"
    __table_args__ = (
        CheckConstraint(
            "resolution IN ('exact', 'needs_confirmation', 'confirmed', 'not_found')",
            name="audit_draft_citations_resolution_chk",
        ),
        CheckConstraint(
            "evidence_source IS NULL OR evidence_source IN "
            "('project_full_text', 'abstract', 'no_text')",
            name="audit_draft_citations_evidence_source_chk",
        ),
        # An unresolved citation has nothing to point at; a resolved one must.
        CheckConstraint(
            "(resolution = 'not_found' AND openalex_id IS NULL) OR "
            "(resolution <> 'not_found')",
            name="audit_draft_citations_not_found_chk",
        ),
        UniqueConstraint("audit_run_id", "ord", name="audit_draft_citations_run_ord_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    audit_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    # The claim the citation is attached to, and the citation as it appears in
    # the draft ("(Koch et al. 2018)"). Both verbatim from the submitted text,
    # so a finding can be shown against the author's own words.
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    citation_text: Mapped[str] = mapped_column(Text, nullable=False)
    # domain/draft_audit.py. Only 'exact' and 'confirmed' proceed to an audit.
    resolution: Mapped[str] = mapped_column(String(30), nullable=False)
    # The matched work. NULL together when resolution is 'not_found'.
    openalex_id: Mapped[str | None] = mapped_column(String(50))
    doi: Mapped[str | None] = mapped_column(String(255))
    matched_title: Mapped[str | None] = mapped_column(Text)
    matched_year: Mapped[int | None] = mapped_column(Integer)
    matched_authors_json: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    # The ranked alternatives, kept so the confirm step has something to offer
    # and so a later reader can see what else was on the table.
    candidates_json: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    # Set only when this project already holds the resolved paper.
    paper_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="SET NULL")
    )
    # Which text the claim was checked against (domain/draft_audit.py). NULL
    # until the citation reaches the evidence step at all.
    evidence_source: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditFinding(Base):
    """One checked piece of evidence: does `quote` actually support the
    statement it's attached to? `sub_id` (e.g. "1.2") is a snapshot of
    domain/citations.py's numbering *as it was when the audit ran* — like
    everywhere else that numbering is used, it's never trusted as a live
    value later, just a convenient label for what this finding was about at
    the time. `target_kind`/`target_id` mirror StatementEvidence's own
    identity columns so a finding always traces back to a real evidence
    row, even after later edits change the numbering."""
    __tablename__ = "audit_findings"
    __table_args__ = (
        CheckConstraint(
            "target_kind IS NULL OR target_kind IN ('paper','passage','dataset','statement')",
            name="audit_findings_target_kind_chk",
        ),
        # A finding belongs to exactly one kind of thing: a statement in a
        # compose document (which always has a real evidence row behind it), or
        # a citation extracted from a pasted draft (which has no statement and
        # no StatementEvidence identity to mirror).
        CheckConstraint(
            "(statement_id IS NOT NULL AND target_kind IS NOT NULL AND target_id IS NOT NULL "
            "AND draft_citation_id IS NULL AND node_review_id IS NULL) OR "
            "(draft_citation_id IS NOT NULL AND statement_id IS NULL "
            "AND target_kind IS NULL AND target_id IS NULL AND node_review_id IS NULL) OR "
            "(node_review_id IS NOT NULL AND statement_id IS NULL AND draft_citation_id IS NULL "
            "AND target_kind IS NULL AND target_id IS NULL)",
            name="audit_findings_anchor_chk",
        ),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('supported', 'misread', 'overreach', "
            "'indeterminate', 'differs_from_source')",
            name="audit_findings_verdict_chk",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="audit_findings_confidence_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    audit_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    statement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("statements.id", ondelete="CASCADE")
    )
    # Set instead of statement_id on a pasted-draft run.
    draft_citation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_draft_citations.id", ondelete="CASCADE")
    )
    # Set instead of either on a Nucleate run: the review whose relevance
    # statement was checked against its own evidence (D-N13).
    node_review_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("node_reviews.id", ondelete="CASCADE")
    )
    sub_id: Mapped[str] = mapped_column(String(20), nullable=False)
    target_kind: Mapped[str | None] = mapped_column(String(20))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quote: Mapped[str | None] = mapped_column(Text)
    # Pass/fail result from before graded verdicts. The Audit panel still reads
    # it until it switches to `verdict`.
    supported: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Graded verdict (domain/audit_verdicts.py). NULL on findings recorded
    # before grading whose pass/fail result can't be split into misread,
    # overreach or indeterminate after the fact.
    verdict: Mapped[str | None] = mapped_column(String(30))
    # Separate from the verdict: the quoted passage itself cites another work.
    secondhand: Mapped[bool | None] = mapped_column(Boolean)
    # The model's own stated confidence in its verdict, 0 to 1. Self-reported,
    # so only meaningful once evaluation shows how well it tracks accuracy.
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    audit_run: Mapped[AuditRun] = relationship(back_populates="findings")


# ── Nucleate: nuclei, the corpus, and the runs that grow it ───────────────────
#
# Vocabulary and the reasoning behind the shape: domain/nucleate.py and
# NUCLEATE_PLAN.md (D-N11..D-N14). In brief: a paper is one CorpusNode per
# project; a LineageEdge records how it joined a nucleus, and membership of a
# nucleus *is* having one; a NodeReview is the user's judgement of a node
# within one nucleus.


class Nucleus(Base):
    """A key question the researcher is gathering evidence about. A project
    can hold several; each has its own chat history, which is its method
    record."""

    __tablename__ = "nuclei"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CorpusNode(Base):
    """One paper in a project's corpus — the same node however many nuclei
    surface it (handoff Part V).

    Keyed on the paper row rather than the DOI, because uploaded PDFs and some
    preprints have no DOI; the DOI is the *export* identifier where one exists.
    Nothing about a node's standing in any nucleus lives here: that is
    NodeReview, per nucleus.
    """

    __tablename__ = "corpus_nodes"
    __table_args__ = (
        UniqueConstraint("project_id", "paper_id", name="corpus_nodes_project_paper_uq"),
        # The target of SemanticEdge's composite foreign keys, which keep both
        # ends of a relation inside the relation's own project.
        UniqueConstraint("id", "project_id", name="corpus_nodes_id_project_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NucleusPrompt(Base):
    """One thing the user asked the archivist to do in a nucleus — the method
    record. Kept alongside the results because replaying prompts will not
    reproduce a graph: retrieval sources and models change (handoff Part VI).
    """

    __tablename__ = "nucleus_prompts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nucleus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nuclei.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # The canvas nodes selected as anchors when the prompt was sent ("Expanding
    # from: Caro 2023"). Empty means the prompt applied to the nucleus itself.
    anchor_node_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NucleusExpansion(Base):
    """The run record for one prompt: what the archivist did, how far it got,
    and what it cost.

    Cost is metered as tokens with the model that consumed them, not as a
    currency figure: prices change, and a stored dollar amount would silently
    become wrong. Per-stage attribution comes from the event log.
    """

    __tablename__ = "nucleus_expansions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'done', 'failed', 'cancelled')",
            name="nucleus_expansions_status_chk",
        ),
        CheckConstraint("budget BETWEEN 1 AND 50", name="nucleus_expansions_budget_chk"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0", name="nucleus_expansions_tokens_chk"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nucleus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nuclei.id", ondelete="CASCADE"), nullable=False
    )
    # One run per prompt: the prompt is the question, this is what happened.
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nucleus_prompts.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # Papers this expansion may add (domain/nucleate.py DEFAULT_EXPANSION_BUDGET).
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    # The current stage (domain/nucleate.py STAGES); NULL before the first.
    stage: Mapped[str | None] = mapped_column(String(30))
    # The archivist's reply in the chat history. Describes papers and why they
    # were chosen; never synthesizes across them (handoff Part III).
    reply_text: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NucleusExpansionEvent(Base):
    """One structured event from an expansion — a stage transition or a
    per-node status change — persisted as it is streamed (handoff Part V).

    A table rather than a JSON list on the run: it is append-only, written
    progressively by the worker, and what cost attribution by stage and any
    later timing analysis will query.
    """

    __tablename__ = "nucleus_expansion_events"
    __table_args__ = (
        UniqueConstraint("expansion_id", "ord", name="nucleus_expansion_events_ord_uq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    expansion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nucleus_expansions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    # Stage state (pending/current/completed/failed) or, with node_id set, a
    # per-node status (fetching/extracting/verifying/ready/needs full text).
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus_nodes.id", ondelete="SET NULL")
    )
    # Real counts, never estimates: {"retrieved": 34}, {"done": 3, "of": 7}.
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    message: Mapped[str | None] = mapped_column(Text)
    # Tokens consumed within this step, for attribution by stage.
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LineageEdge(Base):
    """How a node joined a nucleus: from the nucleus itself or from another
    node, by which prompt, from which source. Always present; never conflated
    with a semantic edge (handoff Part V).

    Membership of a nucleus is *defined* as having a lineage edge in it
    (D-N11) — there is no separate membership list to drift. Every edge
    carries its nucleus, including node-to-node edges, so membership is one
    indexed lookup rather than a graph walk.
    """

    __tablename__ = "lineage_edges"
    __table_args__ = (
        CheckConstraint("from_kind IN ('nucleus', 'node')", name="lineage_edges_from_kind_chk"),
        # From the nucleus: no from-node. From a node: that node, never itself.
        CheckConstraint(
            "(from_kind = 'nucleus' AND from_node_id IS NULL) OR "
            "(from_kind = 'node' AND from_node_id IS NOT NULL AND from_node_id <> to_node_id)",
            name="lineage_edges_from_chk",
        ),
        CheckConstraint(
            "retrieval_source IS NULL OR retrieval_source IN ('library', 'openalex', 'web')",
            name="lineage_edges_retrieval_source_chk",
        ),
        # Retrieved by the archivist (a prompt and a source) or added by a
        # person (neither). Never one without the other.
        CheckConstraint(
            "(prompt_id IS NULL) = (retrieval_source IS NULL)",
            name="lineage_edges_retrieved_or_added_chk",
        ),
        # The target of NodeReview's composite key: lets a review name the edge
        # that introduced it *and* have the database check that edge is in the
        # same nucleus and points at the same node.
        UniqueConstraint("id", "nucleus_id", "to_node_id", name="lineage_edges_identity_uq"),
        Index("lineage_edges_membership_idx", "nucleus_id", "to_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nucleus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nuclei.id", ondelete="CASCADE"), nullable=False
    )
    from_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    from_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus_nodes.id", ondelete="CASCADE")
    )
    to_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus_nodes.id", ondelete="CASCADE"), nullable=False
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nucleus_prompts.id", ondelete="SET NULL")
    )
    # D-N6: on the edge, not the paper — "how this was found" belongs to one
    # expansion. NULL when a person added the node rather than the archivist.
    retrieval_source: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NodeReview(Base):
    """The user's judgement of one node within one nucleus (D-N11): status,
    the relevance statement, why the archivist chose it, and its evidence.

    Per (node, nucleus) because every steering rule is: a paper rejected under
    one question may matter to another, and relevance is always relevance *to
    a question*.

    A review cannot exist without lineage. `introduced_by_edge_id` is part of a
    composite foreign key onto LineageEdge's (id, nucleus_id, to_node_id), so
    the database — not application code — guarantees the introducing edge
    exists, is in this nucleus, and points at this node. That is what keeps
    these rows from becoming the separate membership list the handoff forbids.

    The latest automatic-audit finding for the relevance statement is derived
    by query (AuditFinding.node_review_id) rather than stored here: a stored
    pointer would create a circular foreign key and could drift.
    """

    __tablename__ = "node_reviews"
    __table_args__ = (
        UniqueConstraint("nucleus_id", "node_id", name="node_reviews_nucleus_node_uq"),
        ForeignKeyConstraint(
            ["introduced_by_edge_id", "nucleus_id", "node_id"],
            ["lineage_edges.id", "lineage_edges.nucleus_id", "lineage_edges.to_node_id"],
            name="node_reviews_introduced_by_edge_fk",
        ),
        CheckConstraint(
            "status IN ('frontier', 'accepted', 'rejected', 'pinned')",
            name="node_reviews_status_chk",
        ),
        # A reason belongs to a rejection; un-rejecting clears it.
        CheckConstraint(
            "rejection_reason IS NULL OR status = 'rejected'",
            name="node_reviews_rejection_reason_chk",
        ),
        CheckConstraint(
            "relevance_provenance IS NULL OR relevance_provenance IN "
            "('ai_proposed', 'ai_proposed_accepted', 'ai_proposed_corrected', 'user_set')",
            name="node_reviews_relevance_provenance_chk",
        ),
        # A statement always says who wrote it, and a provenance always has a
        # statement to describe.
        CheckConstraint(
            "(relevance_text IS NULL) = (relevance_provenance IS NULL)",
            name="node_reviews_relevance_pair_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nucleus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("nuclei.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpus_nodes.id", ondelete="CASCADE"), nullable=False
    )
    introduced_by_edge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="frontier")
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    # Describes this paper and why it bears on the question. Never a synthesis
    # across papers and never an answer to the nucleus (handoff Part III).
    relevance_text: Mapped[str | None] = mapped_column(Text)
    relevance_provenance: Mapped[str | None] = mapped_column(String(30))
    # Why the archivist retrieved this paper — "matched the method named in the
    # prompt", "cited by two accepted nodes". Collapsed by default in the drawer.
    selection_reasoning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NodeEvidence(Base):
    """One verbatim quote supporting a node's relevance statement in one
    nucleus.

    The selector is NOT NULL, unlike on Passage and Highlight. Those can hold a
    quote that differs from this server's copy; evidence cannot, because it is
    extracted from text in hand and verified to be a verbatim substring before
    it is ever stored. A quote that can't be anchored isn't evidence. `source`
    says whether it came from the full text or only the abstract — the label
    that tells the user to weigh abstract-level claims accordingly, since
    overreach concentrates there.
    """

    __tablename__ = "node_evidence"
    __table_args__ = (
        UniqueConstraint("review_id", "ord", name="node_evidence_review_ord_uq"),
        CheckConstraint("source IN ('full_text', 'abstract')", name="node_evidence_source_chk"),
        CheckConstraint(
            "jsonb_typeof(text_quote_selector) = 'object' "
            "AND text_quote_selector->>'type' IS NOT DISTINCT FROM 'TextQuoteSelector' "
            "AND text_quote_selector ? 'exact'",
            name="node_evidence_text_quote_selector_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("node_reviews.id", ondelete="CASCADE"), nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_quote_selector: Mapped[dict[str, str]] = mapped_column(
        JSONB(none_as_null=True), nullable=False
    )
    # The stored Passage this came from, when the quote was drawn from the
    # project's full text. NULL for abstract evidence.
    passage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("passages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


_RELATION_SQL = (
    "'uses_method_of', 'uses_data_from', 'extends', "
    "'supports', 'disagrees_with', 'obtains_background_from'"
)


class SemanticEdge(Base):
    """A typed relation between two papers: A (from) uses the method of,
    extends, disagrees with, ... B (to). The six CiTO-mapped terms live in
    domain/relations.py.

    Owned by the project, not a nucleus (D-N20): it is a fact about the two
    papers, shown in every nucleus holding both. Proposed by the archivist only
    on request, from A's own words (D-N19), or set by the user.

    `proposed_relation` keeps what the archivist said, so a correction stays
    visible as one; the constraints below make provenance and proposal agree.
    A rejected edge keeps its row (rejected_at set): it is hidden, and a later
    proposal won't offer it again.
    """

    __tablename__ = "semantic_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_node_id", "project_id"], ["corpus_nodes.id", "corpus_nodes.project_id"],
            ondelete="CASCADE", name="semantic_edges_from_node_fk",
        ),
        ForeignKeyConstraint(
            ["to_node_id", "project_id"], ["corpus_nodes.id", "corpus_nodes.project_id"],
            ondelete="CASCADE", name="semantic_edges_to_node_fk",
        ),
        UniqueConstraint("from_node_id", "to_node_id", "relation",
                         name="semantic_edges_pair_relation_uq"),
        CheckConstraint("from_node_id <> to_node_id", name="semantic_edges_not_self_chk"),
        CheckConstraint(f"relation IN ({_RELATION_SQL})", name="semantic_edges_relation_chk"),
        CheckConstraint(
            f"proposed_relation IS NULL OR proposed_relation IN ({_RELATION_SQL})",
            name="semantic_edges_proposed_relation_chk",
        ),
        CheckConstraint(
            "provenance IN ('ai_proposed', 'ai_proposed_accepted', "
            "'ai_proposed_corrected', 'user_set')",
            name="semantic_edges_provenance_chk",
        ),
        CheckConstraint(
            "(provenance = 'user_set' AND proposed_relation IS NULL) OR "
            "(provenance IN ('ai_proposed', 'ai_proposed_accepted') "
            " AND relation IS NOT DISTINCT FROM proposed_relation) OR "
            "(provenance = 'ai_proposed_corrected' AND proposed_relation IS NOT NULL "
            " AND relation <> proposed_relation)",
            name="semantic_edges_provenance_agrees_chk",
        ),
        CheckConstraint(
            "rejection_reason IS NULL OR rejected_at IS NOT NULL",
            name="semantic_edges_rejection_reason_chk",
        ),
        Index("semantic_edges_project_idx", "project_id"),
        Index("semantic_edges_to_node_idx", "to_node_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    from_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    to_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation: Mapped[str] = mapped_column(String(30), nullable=False)
    provenance: Mapped[str] = mapped_column(String(30), nullable=False)
    proposed_relation: Mapped[str | None] = mapped_column(String(30))
    # The archivist's one line on why its quote shows this relation. Describes
    # what A says about B; never a synthesis of the two.
    rationale: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SemanticEdgeEvidence(Base):
    """A verbatim quote from paper A showing its relation to paper B: the
    sentence where A cites B. Anchored like NodeEvidence — a quote that can't
    be located in A's text is not evidence and is never stored."""

    __tablename__ = "semantic_edge_evidence"
    __table_args__ = (
        UniqueConstraint("edge_id", "ord", name="semantic_edge_evidence_edge_ord_uq"),
        CheckConstraint("source IN ('full_text', 'abstract')",
                        name="semantic_edge_evidence_source_chk"),
        CheckConstraint(
            "jsonb_typeof(text_quote_selector) = 'object' "
            "AND text_quote_selector->>'type' IS NOT DISTINCT FROM 'TextQuoteSelector' "
            "AND text_quote_selector ? 'exact'",
            name="semantic_edge_evidence_text_quote_selector_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    edge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("semantic_edges.id", ondelete="CASCADE"), nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_quote_selector: Mapped[dict[str, str]] = mapped_column(
        JSONB(none_as_null=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
