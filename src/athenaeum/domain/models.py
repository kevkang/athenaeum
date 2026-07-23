from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    orcid: str
    display_name: str | None
    created_at: datetime


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    license: str = "CC-BY-4.0"
    is_public: bool = False


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    is_public: bool | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    license: str
    is_public: bool
    owner_id: UUID
    created_at: datetime


class MemberAdd(BaseModel):
    user_id: UUID
    role: str = Field(default="member", pattern="^(editor|viewer|member)$")


class MemberRoleUpdate(BaseModel):
    role: str = Field(pattern="^(editor|viewer|member)$")


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    role: str
    added_at: datetime


class PublicProjectOut(BaseModel):
    id: UUID
    title: str
    description: str | None
    license: str
    created_at: datetime
    statement_count: int
    locked_count: int
    synthesis_count: int
    avg_verification_rate: float | None


class VerificationRateStats(BaseModel):
    project_id: UUID | None
    synthesis_count: int
    avg_verification_rate: float | None


class PaperIngest(BaseModel):
    doi: str
    project_id: UUID
    found_quote: str | None = None
    found_relevance_note: str | None = None


class PaperUpdate(BaseModel):
    title: str | None = None
    abstract: str | None = None


class PaperOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doi: str | None
    openalex_id: str | None
    title: str
    authors_json: Any
    venue: str | None
    year: int | None
    abstract: str | None
    status: str
    created_at: datetime
    zotero_item_key: str | None = None
    found_quote: str | None = None
    found_relevance_note: str | None = None
    found_corpus: str | None = None


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    ord: int
    char_start: int
    char_end: int


class SentenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    section_id: UUID
    ord: int
    text: str
    char_start: int
    char_end: int


class PassageEvidenceOut(BaseModel):
    """A passage plus enough of its source paper's metadata to render a
    citation (e.g. "Borton et al. 2026") — for the evidence-preview popover."""

    id: UUID
    paper_id: UUID
    verbatim_text: str
    verified: bool
    paper_title: str
    paper_year: int | None
    paper_first_author: str | None


# ── OpenAlex discovery ────────────────────────────────────────────────────────

class OpenAlexSearchResult(BaseModel):
    openalex_id: str | None
    doi: str | None
    title: str
    first_author: str | None
    venue: str | None
    year: int | None
    abstract: str | None
    cited_by_count: int


# ── Search ────────────────────────────────────────────────────────────────────

class SearchMode(str):
    hybrid = "hybrid"
    vector = "vector"
    text = "text"


class SearchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    project_id: UUID
    top_k: int = Field(default=20, ge=1, le=100)
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector|text)$")


class PaperSnippet(BaseModel):
    id: UUID
    title: str
    doi: str | None
    year: int | None
    venue: str | None


class SentenceHit(BaseModel):
    sentence_id: UUID
    text: str
    char_start: int
    char_end: int
    section_label: str
    score: float
    paper: PaperSnippet


class SearchResponse(BaseModel):
    query: str
    mode: str
    hits: list[SentenceHit]


# ── Synthesis ─────────────────────────────────────────────────────────────────

class SynthesisCreate(BaseModel):
    claim_text: str = Field(min_length=10, max_length=5000)
    project_id: UUID
    retrieval_corpus: str = Field(default="project", pattern="^(project|global)$")


class SynthesisUpdate(BaseModel):
    synthesis_text: str | None = None
    query_expansion_json: dict | None = None


class PassageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    paper_id: UUID
    sentence_ids: list[UUID]
    verbatim_text: str
    verified: bool
    verified_at: datetime | None
    checker_model: str | None


class SynthesisStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ord: int
    kind: str
    payload_json: Any
    created_at: datetime


class SynthesisEvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    synthesis_id: UUID
    passage_id: UUID
    role: str
    ord: int


class SynthesisEvidenceFullOut(BaseModel):
    synthesis_id: UUID
    passage_id: UUID
    role: str
    ord: int
    verbatim_text: str
    verified: bool
    paper_id: UUID
    paper_title: str
    paper_doi: str | None
    paper_year: int | None
    paper_venue: str | None
    paper_first_author: str | None = None
    rationale: str | None = None


class SynthesisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    claim_text: str
    synthesis_text: str | None
    query_expansion_json: Any
    retrieval_corpus: str
    model: str
    checker_model: str | None
    verification_rate: float | None
    status: str
    run_at: datetime


class EvaluationCreate(BaseModel):
    passage_id: UUID
    stance: str = Field(pattern="^(agree|partial|disagree)$")
    body: str | None = None


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    synthesis_id: UUID
    passage_id: UUID
    user_id: UUID
    stance: str
    body: str | None
    created_at: datetime


# ── LLM credentials ───────────────────────────────────────────────────────────

class LLMCredentialsIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=300)


class LLMCredentialsStatus(BaseModel):
    connected: bool
    provider: str | None = None


# ── Statements ────────────────────────────────────────────────────────────────

_QUESTION_STATUSES = {"open", "resolved", "wontfix"}
_STATEMENT_EDGE_RELATIONS = "^(relates_to|supports|tension)$"


class StatementCreate(BaseModel):
    project_id: UUID
    type: str = Field(min_length=1, max_length=50)
    assertion: str = Field(min_length=1)
    built_from_synthesis_id: UUID | None = None


class StatementUpdate(BaseModel):
    assertion: str | None = None
    type: str | None = None
    status: str | None = Field(default=None, pattern="^(open|resolved|wontfix)$")


class EvidenceCreate(BaseModel):
    target_kind: str = Field(pattern="^(paper|passage|dataset|statement)$")
    target_id: UUID
    role: str = Field(default="evidence", max_length=50)


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    statement_id: UUID
    target_kind: str
    target_id: UUID
    role: str


class StatementEdgeCreate(BaseModel):
    to_statement_id: UUID
    relation: str = Field(pattern=_STATEMENT_EDGE_RELATIONS)


class StatementEdgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_statement_id: UUID
    to_statement_id: UUID
    relation: str
    created_by: UUID | None
    created_at: datetime


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    author_id: UUID | None
    type: str
    status: str
    assertion: str
    locked_at: datetime | None
    drafted_at: datetime
    built_from_synthesis_id: UUID | None


# ── Annotations ───────────────────────────────────────────────────────────────

class AnnotationCreate(BaseModel):
    project_id: UUID
    target_kind: str = Field(pattern="^(paper|passage|statement|sentence|section)$")
    target_id: UUID
    body: str = Field(min_length=1)


class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    author_id: UUID
    target_kind: str
    target_id: UUID
    body: str
    created_at: datetime


# ── Datasets ──────────────────────────────────────────────────────────────────

# ── Graph ─────────────────────────────────────────────────────────────────────

class StatementNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    type: str
    status: str
    assertion: str
    evidence_count: int


class StatementGraphOut(BaseModel):
    nodes: list[StatementNodeOut]
    edges: list[StatementEdgeOut]


class GhostPaperOut(BaseModel):
    id: UUID
    doi: str | None
    title: str | None
    year: int | None
    venue: str | None


class CoCitationNeighborOut(BaseModel):
    id: UUID
    doi: str | None
    title: str | None
    year: int | None
    venue: str | None
    shared_citations: int


# ── Knowledge links ───────────────────────────────────────────────────────────

_REF_TYPES = "^(paper|synthesis)$"
_REL_TYPES = "^(supports|contradicts|extends|cites|related)$"


class KnowledgeLinkCreate(BaseModel):
    project_id: UUID
    from_type: str = Field(pattern=_REF_TYPES)
    from_id: UUID
    to_type: str = Field(pattern=_REF_TYPES)
    to_id: UUID
    rel: str = Field(pattern=_REL_TYPES)
    note: str | None = Field(default=None, max_length=2000)


class KnowledgeLinkOut(BaseModel):
    id: UUID
    project_id: UUID
    from_type: str
    from_id: UUID
    from_title: str
    to_type: str
    to_id: UUID
    to_title: str
    rel: str
    note: str | None
    created_by: UUID
    created_at: datetime


class KnowledgeRefOut(BaseModel):
    id: UUID
    ref_type: str   # 'paper' | 'synthesis'
    title: str
    subtitle: str | None  # year + venue for papers; status for syntheses


# ── Datasets ──────────────────────────────────────────────────────────────────

class DatasetCreate(BaseModel):
    project_id: UUID
    kind: str = Field(pattern="^(figure|table|synthesis|raw)$")
    title: str = Field(min_length=1, max_length=500)
    storage_uri: str | None = None


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    kind: str
    title: str
    storage_uri: str | None
    created_at: datetime


# ── Events / history ─────────────────────────────────────────────────────────

class EventOut(BaseModel):
    id: UUID
    project_seq: int
    actor_id: UUID | None
    actor_display_name: str | None
    kind: str
    entity_kind: str
    entity_id: UUID | None
    prev_json: dict[str, Any] | None
    payload_json: dict[str, Any]
    created_at: datetime


class HistoryPage(BaseModel):
    events: list[EventOut]
    next_before_seq: int | None

# ── Zotero ────────────────────────────────────────────────────────────────────

class ZoteroCredentialsIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)


class ZoteroCredentialsStatus(BaseModel):
    connected: bool
    zotero_user_id: str | None = None


class ZoteroSearchResultOut(BaseModel):
    item_key: str
    title: str
    creators: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    item_type: str
    already_in_project: bool
    date_added: str | None = None


class ZoteroImportRequest(BaseModel):
    project_id: UUID
    zotero_item_key: str = Field(min_length=1, max_length=50)
    found_quote: str | None = None
    found_relevance_note: str | None = None


class ZoteroCollectionOut(BaseModel):
    key: str
    name: str
    parent_key: str | None


class ZoteroBrowseResponse(BaseModel):
    items: list[ZoteroSearchResultOut]
    total: int


class ZoteroFindRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    project_id: UUID
    corpus: str = Field(pattern="^(zotero_sync|zotero_local)$")


class ZoteroFindResultOut(BaseModel):
    item_key: str
    title: str
    creators: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    item_type: str
    already_in_project: bool
    quote: str
    relevance_note: str
    model: str


# ── OpenAlex find ─────────────────────────────────────────────────────────────

class OpenAlexFindRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    project_id: UUID


class OpenAlexFindResultOut(BaseModel):
    openalex_id: str | None
    doi: str | None
    title: str
    creators: list[str]
    year: int | None
    venue: str | None
    cited_by_count: int
    already_in_project: bool
    quote: str
    relevance_note: str
    model: str


# ── Web search find ───────────────────────────────────────────────────────────

class WebSearchFindRequest(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    project_id: UUID


class WebSearchFindResultOut(BaseModel):
    url: str
    title: str
    source: str | None
    quote: str
    relevance_note: str
    model: str


# ── Evidence sentences (Read-view highlighting) ───────────────────────────────

class SentenceEvidenceOut(BaseModel):
    sentence_id: UUID
    statement_id: UUID
    assertion: str
    role: str


# ── Backtrace ─────────────────────────────────────────────────────────────────

class BacktraceStartRequest(BaseModel):
    project_id: UUID
    paper_id: UUID
    sentence_id: UUID | None = None
    claim_text: str = Field(min_length=3, max_length=2000)


class BacktracePickRequest(BaseModel):
    openalex_id: str = Field(min_length=1, max_length=100)


class BacktraceCandidateOut(BaseModel):
    openalex_id: str
    title: str
    authors: list[str]
    year: int | None
    doi: str | None
    venue: str | None


class BacktraceHopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chain_id: UUID
    hop_index: int
    to_openalex_id: str
    to_doi: str | None
    to_title: str
    to_authors_json: Any
    to_year: int | None
    to_venue: str | None
    to_paper_id: UUID | None
    quote: str | None
    quote_source: str | None
    relevance_note: str | None
    match_reasoning: str | None
    model: str | None
    resolution_status: str
    user_decision: str
    created_at: datetime


class BacktraceChainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    origin_paper_id: UUID
    origin_sentence_id: UUID | None
    origin_text: str
    created_at: datetime


class BacktraceChainSummaryOut(BaseModel):
    id: UUID
    origin_paper_id: UUID
    origin_text: str
    hop_count: int
    created_at: datetime


class BacktraceChainDetailOut(BaseModel):
    chain: BacktraceChainOut
    hops: list[BacktraceHopOut]


class BacktraceStepResult(BaseModel):
    """Response for start/continue/pick: either a resolved (or in-progress)
    hop, or — when the LLM couldn't confidently identify a reference — a list
    of candidates for the user to manually choose from instead."""

    chain: BacktraceChainOut
    hop: BacktraceHopOut | None = None
    candidates: list[BacktraceCandidateOut] | None = None
    message: str | None = None
    # Set only when `message` is itself LLM-authored (the identification
    # step's stated reasoning) — None for static system messages ("no
    # references to trace", etc.), so the UI knows whether to attribute it.
    model: str | None = None


class ZoteroLocalStatus(BaseModel):
    available: bool
    relative_path: str | None = None


class ZoteroLocalSelectRequest(BaseModel):
    # Relative to ZOTERO_LOCAL_HOST_MOUNT (the operator's bind-mounted home
    # directory) — e.g. "Zotero" for the common ~/Zotero default.
    relative_path: str = Field(min_length=1, max_length=500)


class ZoteroLocalDirEntry(BaseModel):
    name: str
    has_zotero_sqlite: bool


class ZoteroLocalBrowseDirsResponse(BaseModel):
    path: str
    parent: str | None
    entries: list[ZoteroLocalDirEntry]


class ZoteroBrowserImportItemOut(BaseModel):
    item_key: str
    title: str
    creators: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    item_type: str
    collection_keys: list[str]
    # Relative to the Zotero data directory the browser picked, e.g.
    # "storage/ABCD1234/paper.pdf" — the browser resolves this against its
    # own directory handle; the server never sees the actual file.
    attachment_relative_path: str | None
    already_in_project: bool


class ZoteroBrowserImportCollectionOut(BaseModel):
    key: str
    name: str
    parent_key: str | None


class ZoteroBrowserImportParseResponse(BaseModel):
    items: list[ZoteroBrowserImportItemOut]
    collections: list[ZoteroBrowserImportCollectionOut]
