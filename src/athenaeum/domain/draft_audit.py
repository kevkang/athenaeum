"""Vocabulary for auditing a pasted draft: how a citation was resolved, and
what text its claim was checked against.

Kept in one module for the same reason as domain/audit_verdicts.py and
domain/relations.py — the pipeline, the API, the database CHECK constraints and
the UI copy all have to mean the same thing by these words.

These states exist because a draft audit has two failure modes the compose-document
audit does not, and conflating them would be the worst possible outcome for a tool
whose whole purpose is telling an author which of their citations do not hold up:

- A citation that could not be resolved to a paper was never checked. It is not
  a citation that failed.
- A citation resolved to the *wrong* paper produces a confident verdict about
  something the author never cited. This is why `EXACT` is deliberately narrow:
  it is a deterministic string match, never a model's opinion that a candidate
  looks right. Everything a model merely ranked goes to NEEDS_CONFIRMATION and
  waits for a person.
"""
from __future__ import annotations

from typing import Final, Literal

# ── How a citation in the draft was matched to a real work ───────────────────

EXACT: Final = "exact"
NEEDS_CONFIRMATION: Final = "needs_confirmation"
CONFIRMED: Final = "confirmed"
NOT_FOUND: Final = "not_found"

Resolution = Literal["exact", "needs_confirmation", "confirmed", "not_found"]

RESOLUTIONS: tuple[Resolution, ...] = (EXACT, NEEDS_CONFIRMATION, CONFIRMED, NOT_FOUND)

RESOLUTION_DEFINITIONS: dict[Resolution, str] = {
    EXACT: (
        "Matched deterministically: a DOI written in the draft itself, or a candidate "
        "whose title matches the cited title exactly once normalised. No model judgement "
        "is involved, which is why this is the only resolution that proceeds to an audit "
        "without a person confirming it."
    ),
    NEEDS_CONFIRMATION: (
        "Candidates were found and ranked, but none matched deterministically. Nothing is "
        "audited until someone picks one, because auditing a claim against the wrong paper "
        "produces a confident verdict about a work the author never cited."
    ),
    CONFIRMED: "A person picked one of the candidates. Audited the same as an exact match.",
    NOT_FOUND: (
        "No plausible candidate was found for this citation. The claim was never checked, "
        "which is not the same as a claim that failed its check."
    ),
}

# Resolutions whose citation has a work to audit against. NEEDS_CONFIRMATION is
# absent on purpose: it is a question put to the user, not a result.
AUDITABLE_RESOLUTIONS: tuple[Resolution, ...] = (EXACT, CONFIRMED)


# ── What text the claim was actually checked against ─────────────────────────

PROJECT_FULL_TEXT: Final = "project_full_text"
ABSTRACT: Final = "abstract"
NO_TEXT: Final = "no_text"

EvidenceSource = Literal["project_full_text", "abstract", "no_text"]

EVIDENCE_SOURCES: tuple[EvidenceSource, ...] = (PROJECT_FULL_TEXT, ABSTRACT, NO_TEXT)

EVIDENCE_SOURCE_DEFINITIONS: dict[EvidenceSource, str] = {
    PROJECT_FULL_TEXT: (
        "The cited paper is already ingested in this project, so the quote comes from its "
        "body text."
    ),
    ABSTRACT: (
        "The quote comes from the work's abstract. Audit evaluation found that overreach "
        "concentrates at abstract level, so a finding drawn from an abstract has to say so "
        "wherever it is shown."
    ),
    NO_TEXT: (
        "The work was resolved but neither its full text nor an abstract was available, so "
        "there was nothing to check the claim against. Not a failed check."
    ),
}

# Sources that actually yielded text to quote from.
SOURCES_WITH_TEXT: tuple[EvidenceSource, ...] = (PROJECT_FULL_TEXT, ABSTRACT)


# ── Which trigger produced an audit run ──────────────────────────────────────

COMPOSE_DOCUMENT: Final = "compose_document"
PASTED_DRAFT: Final = "pasted_draft"

AuditRunKind = Literal["compose_document", "pasted_draft"]

AUDIT_RUN_KINDS: tuple[AuditRunKind, ...] = (COMPOSE_DOCUMENT, PASTED_DRAFT)

AUDIT_RUN_KIND_DEFINITIONS: dict[AuditRunKind, str] = {
    COMPOSE_DOCUMENT: (
        "Audit of the project's own compose document, where each claim already has "
        "evidence attached to it."
    ),
    PASTED_DRAFT: (
        "Audit of text pasted into the Audit tab, where the citations have to be extracted "
        "and resolved first. The entry point that needs no corpus."
    ),
}
