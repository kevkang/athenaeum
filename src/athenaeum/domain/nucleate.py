"""Nucleate's vocabulary: names and definitions for nuclei, the corpus, and
the runs that grow it.

Kept in one module for the same reason as audit_verdicts.py, relations.py and
draft_audit.py — the pipeline, the API, the database CHECK constraints, the UI
copy and the export all have to mean the same thing by these words. Every
value here is either specified by the Nucleate handoff or was decided with the
user (NUCLEATE_PLAN.md, D-N11..D-N14); none was invented to fill a gap.

The shape (D-N11): a paper is one **corpus node** per project, however many
nuclei surface it. A **lineage edge** records how a node joined a nucleus —
from the nucleus itself or from another node, by which prompt, from which
source — and membership of a nucleus *is* having a lineage edge there. A
**node review** holds the user's judgement of a node within one nucleus:
status, relevance, selection reasoning, evidence. Review state is per (node,
nucleus) because every steering rule is: a paper rejected under one question
may matter to another.
"""
from __future__ import annotations

from typing import Final, Literal

# ── Review status, per (node, nucleus) — handoff Part VI ──────────────────────

FRONTIER: Final = "frontier"
ACCEPTED: Final = "accepted"
REJECTED: Final = "rejected"
PINNED: Final = "pinned"

ReviewStatus = Literal["frontier", "accepted", "rejected", "pinned"]
REVIEW_STATUSES: tuple[ReviewStatus, ...] = (FRONTIER, ACCEPTED, REJECTED, PINNED)

REVIEW_STATUS_DEFINITIONS: dict[ReviewStatus, str] = {
    FRONTIER: "Proposed by the archivist and not yet reviewed. Provisional.",
    ACCEPTED: "Reviewed and kept in this nucleus.",
    REJECTED: (
        "Reviewed and set aside for this nucleus, with an optional reason. Retained, not "
        "deleted: it is steering signal, and the archivist must not propose this paper "
        "again under the same nucleus. It says nothing about any other nucleus."
    ),
    PINNED: (
        "Accepted, and always considered when interpreting later prompts for this nucleus."
    ),
}


# ── Provenance of AI-originated text and edges (D-N12) ────────────────────────
#
# One vocabulary for relevance statements and semantic edges alike, so an
# export reader learns four words, not two parallel sets.

AI_PROPOSED: Final = "ai_proposed"
AI_PROPOSED_ACCEPTED: Final = "ai_proposed_accepted"
AI_PROPOSED_CORRECTED: Final = "ai_proposed_corrected"
USER_SET: Final = "user_set"

Provenance = Literal["ai_proposed", "ai_proposed_accepted", "ai_proposed_corrected", "user_set"]
PROVENANCES: tuple[Provenance, ...] = (
    AI_PROPOSED, AI_PROPOSED_ACCEPTED, AI_PROPOSED_CORRECTED, USER_SET,
)

PROVENANCE_DEFINITIONS: dict[Provenance, str] = {
    AI_PROPOSED: (
        "Written by the archivist and not yet reviewed. The only provenance shown as AI "
        "text (blue italics), at every zoom level."
    ),
    AI_PROPOSED_ACCEPTED: "Written by the archivist and accepted by a person unchanged.",
    AI_PROPOSED_CORRECTED: "Written by the archivist and then edited by a person.",
    USER_SET: "Written by a person from the start.",
}

# Provenances a person has taken responsibility for; everything but ai_proposed.
HUMAN_REVIEWED: tuple[Provenance, ...] = (AI_PROPOSED_ACCEPTED, AI_PROPOSED_CORRECTED, USER_SET)


# ── Where a node was retrieved from — handoff Part V, on the lineage edge (D-N6)

LIBRARY: Final = "library"
OPENALEX: Final = "openalex"
WEB: Final = "web"

RetrievalSource = Literal["library", "openalex", "web"]
RETRIEVAL_SOURCES: tuple[RetrievalSource, ...] = (LIBRARY, OPENALEX, WEB)

RETRIEVAL_SOURCE_DEFINITIONS: dict[RetrievalSource, str] = {
    LIBRARY: "The user's own library: Zotero and manual imports. Preferred when relevance "
             "is comparable, since full text is usually already available.",
    OPENALEX: "OpenAlex.",
    WEB: "Web search.",
}
# A lineage edge with no retrieval source was added by a person — "Add to
# corpus" from Backtrace, say — rather than retrieved by the archivist. That is
# an absence, not a fourth source, so it is NULL rather than a value here.


# ── What a verbatim quote was taken from — handoff Part VI ────────────────────
#
# The same two words BacktraceHop.quote_source already uses.

FULL_TEXT: Final = "full_text"
ABSTRACT: Final = "abstract"

EvidenceSource = Literal["full_text", "abstract"]
EVIDENCE_SOURCES: tuple[EvidenceSource, ...] = (FULL_TEXT, ABSTRACT)


# ── What a lineage edge starts from ──────────────────────────────────────────

FROM_NUCLEUS: Final = "nucleus"
FROM_NODE: Final = "node"

LineageFrom = Literal["nucleus", "node"]
LINEAGE_FROMS: tuple[LineageFrom, ...] = (FROM_NUCLEUS, FROM_NODE)


# ── Expansion runs ───────────────────────────────────────────────────────────

RUNNING: Final = "running"
DONE: Final = "done"
FAILED: Final = "failed"
CANCELLED: Final = "cancelled"

ExpansionStatus = Literal["running", "done", "failed", "cancelled"]
EXPANSION_STATUSES: tuple[ExpansionStatus, ...] = (RUNNING, DONE, FAILED, CANCELLED)

EXPANSION_STATUS_DEFINITIONS: dict[ExpansionStatus, str] = {
    RUNNING: "In progress.",
    DONE: "Finished every stage and is awaiting the user's review.",
    FAILED: "Stopped by an error. Nodes completed before it are kept.",
    CANCELLED: (
        "Stopped by the user. Nodes completed before it are kept as frontier nodes. "
        "Distinct from failed so that a failure rate computed from run records counts "
        "only things that went wrong (D-N14)."
    ),
}

# The stages of one expansion, in order — handoff Part V's table. The last is
# the explicit, visible "the next move is yours" state, not the absence of a
# spinner.
STAGES: tuple[str, ...] = (
    "interpreting",
    "retrieving",
    "ranking",
    "ingesting",
    "extracting",
    "interpreting_papers",
    "verifying",
    "placing",
    "awaiting_review",
)

STAGE_LABELS: dict[str, str] = {
    "interpreting": "Interpreting",
    "retrieving": "Retrieving",
    "ranking": "Ranking",
    "ingesting": "Ingesting",
    "extracting": "Extracting",
    "interpreting_papers": "Interpreting papers",
    "verifying": "Verifying",
    "placing": "Placing",
    "awaiting_review": "Awaiting review",
}

# How far along a stage is, as shown in the stage list — handoff Part V.
STAGE_STATES: tuple[str, ...] = ("pending", "current", "completed", "failed")

# Default papers per expansion, configurable per project. Force-directed
# layouts become hard to read past about fifty nodes, and unbounded expansion
# gets there in a few steps.
DEFAULT_EXPANSION_BUDGET: Final = 6
MIN_EXPANSION_BUDGET: Final = 5
MAX_EXPANSION_BUDGET: Final = 8
