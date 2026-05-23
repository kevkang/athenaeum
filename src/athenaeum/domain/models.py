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
    brick_count: int
    cemented_count: int
    synthesis_count: int
    avg_verification_rate: float | None


class VerificationRateStats(BaseModel):
    project_id: UUID | None
    synthesis_count: int
    avg_verification_rate: float | None


class PaperIngest(BaseModel):
    doi: str
    project_id: UUID


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


# ── Bricks ────────────────────────────────────────────────────────────────────

class BrickCreate(BaseModel):
    project_id: UUID
    type: str = Field(min_length=1, max_length=50)
    assertion: str = Field(min_length=1)
    built_from_synthesis_id: UUID | None = None


class BrickUpdate(BaseModel):
    assertion: str | None = None
    type: str | None = None


class RestsOnCreate(BaseModel):
    target_kind: str = Field(pattern="^(paper|passage|dataset|brick)$")
    target_id: UUID
    role: str = Field(default="evidence", max_length=50)


class RestsOnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    brick_id: UUID
    target_kind: str
    target_id: UUID
    role: str


class BuildsOnCreate(BaseModel):
    parent_brick_id: UUID
    ord: int = 0


class BuildsOnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    brick_id: UUID
    parent_brick_id: UUID
    ord: int


class BrickOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    type: str
    status: str
    assertion: str
    cemented_at: datetime | None
    drafted_at: datetime
    built_from_synthesis_id: UUID | None


# ── Annotations & questions ───────────────────────────────────────────────────

class AnnotationCreate(BaseModel):
    project_id: UUID
    target_kind: str = Field(pattern="^(paper|passage|brick|sentence|section)$")
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


class QuestionCreate(BaseModel):
    project_id: UUID
    body: str = Field(min_length=1)
    attached_to: list[dict[str, Any]] = Field(default_factory=list)


class QuestionUpdate(BaseModel):
    status: str = Field(pattern="^(open|resolved|wontfix)$")


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    author_id: UUID
    body: str
    status: str
    attached_to: Any
    created_at: datetime


# ── Datasets ──────────────────────────────────────────────────────────────────

# ── Graph ─────────────────────────────────────────────────────────────────────

class BrickNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    type: str
    status: str
    assertion: str


class GraphBuildsOnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    brick_id: UUID
    parent_brick_id: UUID
    ord: int


class GraphRestsOnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    brick_id: UUID
    target_kind: str
    target_id: UUID
    role: str


class BrickGraphOut(BaseModel):
    nodes: list[BrickNodeOut]
    builds_on: list[GraphBuildsOnOut]
    rests_on: list[GraphRestsOnOut]


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
