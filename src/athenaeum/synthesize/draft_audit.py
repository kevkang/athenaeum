"""Turning a pasted draft into auditable claim/citation pairs.

Three steps, kept separate because they fail in different ways and a user has
to be able to tell those failures apart (see domain/draft_audit.py):

1. **Extract** — a model reads the draft and returns the claims that carry
   citations, each with the citation the author gave for it.
2. **Resolve** — match each citation to a real work. A DOI written in the
   draft, or a title that appears verbatim in the citation, resolves
   deterministically and proceeds on its own. Anything a model merely ranked
   stops and waits for a person, because auditing a claim against the wrong
   paper produces a confident verdict about a work the author never cited.
3. **Evidence** — find a quote to check the claim against, from the project's
   own full text when it happens to hold the paper, otherwise the abstract.

Only then does the existing engine (synthesize/audit.py's audit_evidence) run,
unchanged: it takes a claim and quotes and judges them, and does not care where
they came from.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.config import Settings
from athenaeum.domain.draft_audit import (
    ABSTRACT,
    AUDITABLE_RESOLUTIONS,
    EXACT,
    NEEDS_CONFIRMATION,
    NO_TEXT,
    NOT_FOUND,
    PROJECT_FULL_TEXT,
    EvidenceSource,
    Resolution,
)
from athenaeum.ingest.openalex import OpenAlexSearchHit, search_works
from athenaeum.provider.base import Message, Provider
from athenaeum.provider.resolver import get_provider_for_user
from athenaeum.storage.models import AuditDraftCitation, AuditFinding, AuditRun, Paper, ProjectPaper
from athenaeum.synthesize.audit import audit_evidence, malformed_rate
from athenaeum.synthesize.backtrace import search_full_text_for_claim
from athenaeum.synthesize.find_ranking import FindCandidate, rank_candidates
from athenaeum.synthesize.prompts import EXTRACT_CITATIONS_SYSTEM, EXTRACT_CITATIONS_TOOL

log = structlog.get_logger()

# A DOI as it appears in running text. Deliberately conservative about the
# trailing character class: a DOI at the end of a sentence must not swallow the
# full stop, and one in parentheses must not swallow the closing bracket.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]*[A-Za-z0-9])", re.IGNORECASE)

# Below this length a title match is not evidence of anything — "Introduction"
# or "Soil respiration" would match dozens of works. Anything shorter is sent
# to a person instead of resolved deterministically.
_MIN_EXACT_TITLE_CHARS = 25


@dataclass(frozen=True)
class ExtractedCitation:
    """One claim from the draft and the citation the author attached to it.

    `citation_text` is the fullest form the draft gives: the reference-list
    entry when there is one, otherwise the inline marker. Resolution works far
    better with the former, and the claim sentence still shows the reader the
    marker in context.
    """

    claim_text: str
    citation_text: str


@dataclass
class ResolvedCitation:
    resolution: Resolution
    match: OpenAlexSearchHit | None = None
    candidates: list[OpenAlexSearchHit] = field(default_factory=list)


def find_doi(text: str) -> str | None:
    """The first DOI written in `text`, lowercased, or None.

    A DOI in the draft is the one case where the author has said exactly which
    work they mean, so it is the strongest deterministic signal available.
    """
    match = _DOI_RE.search(text)
    return match.group(1).lower().rstrip(".") if match else None


def normalize_title(title: str) -> str:
    """Fold a title to a form two spellings of the same title share.

    Case, accents, punctuation and runs of whitespace all vary between a
    reference list and a metadata record without meaning anything. Word content
    and order do not, so they are all that survives.
    """
    folded = unicodedata.normalize("NFKD", title)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.casefold()
    folded = re.sub(r"[^a-z0-9\s]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def pick_exact_match(
    citation_text: str, hits: list[OpenAlexSearchHit]
) -> OpenAlexSearchHit | None:
    """The one hit whose title appears verbatim in the citation, or None.

    Returns None when several hits match, not just when none do: two works
    whose titles both appear leaves genuine ambiguity, and guessing between
    them is exactly what this function exists to avoid. Short titles are never
    matched this way — see _MIN_EXACT_TITLE_CHARS.
    """
    haystack = normalize_title(citation_text)
    matches = [
        h for h in hits
        if (t := normalize_title(h.title))
        and len(t) >= _MIN_EXACT_TITLE_CHARS
        and t in haystack
    ]
    return matches[0] if len(matches) == 1 else None


async def extract_citations(provider: Provider, draft_text: str) -> list[ExtractedCitation]:
    """Ask a model for the draft's claim/citation pairs.

    Returns an empty list rather than raising when the model gives nothing
    usable: a draft with no extractable citations is a real outcome the user
    needs told plainly, not an error.
    """
    if not draft_text.strip():
        return []

    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=draft_text)],
        system=EXTRACT_CITATIONS_SYSTEM,
        max_tokens=4096,
        tools=[EXTRACT_CITATIONS_TOOL],
    )
    if tool_result is None:
        log.warning("draft_audit.extraction_no_tool_result")
        return []

    raw = tool_result["input"].get("citations", [])
    if not isinstance(raw, list):
        log.warning("draft_audit.extraction_malformed", got=type(raw).__name__)
        return []

    citations: list[ExtractedCitation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        claim = str(entry.get("claim_text") or "").strip()
        citation = str(entry.get("citation_text") or "").strip()
        # Both halves are required: a claim with no citation is not something
        # this tool can check, and a citation with no claim has nothing to
        # check it against.
        if claim and citation:
            citations.append(ExtractedCitation(claim_text=claim, citation_text=citation))
    return citations


async def resolve_citation(
    citation: ExtractedCitation,
    *,
    per_page: int = 8,
    email: str = "contact@athenaeum.app",
) -> ResolvedCitation:
    """Match one citation to a work, deterministically where possible.

    Order matters. A DOI in the draft wins outright. Otherwise the citation is
    searched and a single verbatim title match wins. Everything else comes back
    as candidates for a person to choose between, or as not_found.
    """
    loop = asyncio.get_running_loop()
    doi = find_doi(citation.citation_text)

    query = doi or citation.citation_text
    try:
        hits = await loop.run_in_executor(None, search_works, query, per_page, email)
    except Exception as exc:  # network, rate limit, malformed query
        log.warning("draft_audit.search_failed", error=str(exc)[:200])
        return ResolvedCitation(resolution=NOT_FOUND)

    if not hits:
        return ResolvedCitation(resolution=NOT_FOUND)

    if doi:
        # The author named the work outright; trust it only if the search
        # actually returned that DOI rather than merely something similar.
        for hit in hits:
            if hit.doi and hit.doi.lower() == doi:
                return ResolvedCitation(resolution=EXACT, match=hit, candidates=hits)

    exact = pick_exact_match(citation.citation_text, hits)
    if exact is not None:
        return ResolvedCitation(resolution=EXACT, match=exact, candidates=hits)

    return ResolvedCitation(resolution=NEEDS_CONFIRMATION, candidates=hits)


async def find_project_paper(
    session: AsyncSession, project_id: uuid.UUID, hit: OpenAlexSearchHit
) -> Paper | None:
    """The project's own ingested copy of `hit`, if it happens to have one.

    Matched on DOI or OpenAlex id, both unique on Paper. Only a `full_text`
    paper counts: a ghost row is metadata with no body to quote, which is what
    the abstract path is for. Nothing is created here — resolving a citation
    must never add a paper to someone's project.
    """
    conditions = []
    if hit.doi:
        conditions.append(Paper.doi == hit.doi)
    if hit.openalex_id:
        conditions.append(Paper.openalex_id == hit.openalex_id)
    if not conditions:
        return None

    return await session.scalar(
        select(Paper)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(
            ProjectPaper.project_id == project_id,
            Paper.status == "full_text",
            or_(*conditions),
        )
        .limit(1)
    )


async def select_evidence(
    provider: Provider,
    session: AsyncSession,
    project_id: uuid.UUID,
    claim: str,
    hit: OpenAlexSearchHit,
    *,
    embed_model: str,
) -> tuple[str | None, EvidenceSource]:
    """Find a quote to check `claim` against, and say where it came from.

    Prefers the project's own full text when it holds the paper, because a
    body is better evidence than an abstract; falls back to the abstract, which
    is all a user with no corpus will ever have. Returns (None, NO_TEXT) when
    neither yields anything — a claim that was never checked, which the caller
    must not record as a claim that failed.
    """
    paper = await find_project_paper(session, project_id, hit)
    if paper is not None:
        passage = await search_full_text_for_claim(
            provider, session, project_id, paper.id, claim, embed_model
        )
        if passage is not None:
            return passage.verbatim_text, PROJECT_FULL_TEXT
        # The project has the paper but nothing in it matched. Fall through to
        # the abstract rather than giving up: the abstract may still carry the
        # claim, and saying "no text" here would be false.

    if hit.abstract:
        candidate = FindCandidate(
            title=hit.title,
            creators=[hit.first_author] if hit.first_author else [],
            year=hit.year,
            venue=hit.venue,
            doi=hit.doi,
            abstract=hit.abstract,
            source=hit,
        )
        # rank_candidates discards any quote that is not a verbatim substring
        # of the abstract, so a fabricated quote cannot reach the audit.
        matches = await rank_candidates(provider, claim, [candidate])
        if matches:
            return matches[0].quote, ABSTRACT

    return None, NO_TEXT


def _citation_event(citation: AuditDraftCitation) -> dict[str, Any]:
    """The wire shape for one resolved citation.

    Published before any verdict exists, so the reader sees what was matched
    and what is still waiting on them, rather than only the citations that got
    as far as being judged.
    """
    return {
        "id": str(citation.id),
        "ord": citation.ord,
        "claim_text": citation.claim_text,
        "citation_text": citation.citation_text,
        "resolution": citation.resolution,
        "openalex_id": citation.openalex_id,
        "doi": citation.doi,
        "matched_title": citation.matched_title,
        "matched_year": citation.matched_year,
        "evidence_source": citation.evidence_source,
    }


def _candidate_payload(hits: list[OpenAlexSearchHit]) -> list[dict[str, Any]]:
    """The alternatives offered for a citation awaiting confirmation."""
    return [
        {
            "openalex_id": h.openalex_id,
            "doi": h.doi,
            "title": h.title,
            "first_author": h.first_author,
            "venue": h.venue,
            "year": h.year,
            "cited_by_count": h.cited_by_count,
        }
        for h in hits
    ]


async def run_draft_audit(
    audit_run_id: uuid.UUID,
    engine: AsyncEngine,
    redis: aioredis.Redis,
    settings: Settings,
    requesting_user_id: uuid.UUID,
) -> None:
    """Audit a pasted draft end to end.

    Mirrors run_audit()'s shape — same channel, same per-row publish-then-commit,
    same response-integrity threshold — but every claim needs its citation found
    and resolved first, and each of those steps can fail in a way that is *not*
    a failed citation. Those outcomes are recorded on the citation row and
    produce no finding at all, so nothing in the results can be mistaken for a
    verdict that was never reached.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        audit_run = await session.get(AuditRun, audit_run_id)
        if audit_run is None:
            log.error("draft_audit.not_found", audit_run_id=str(audit_run_id))
            return

        channel = f"audit:{audit_run_id}:steps"
        ord_counter = 0
        quotes_checked = 0
        malformed_entries = 0
        try:
            provider = await get_provider_for_user(
                session, requesting_user_id, settings.checker_model, settings
            )
            log.info("draft_audit.start", audit_run_id=str(audit_run_id))

            citations = await extract_citations(provider, audit_run.draft_text or "")
            log.info(
                "draft_audit.extracted",
                audit_run_id=str(audit_run_id),
                citations=len(citations),
            )

            for ord_, extracted in enumerate(citations):
                resolved = await resolve_citation(extracted)
                match = resolved.match

                row = AuditDraftCitation(
                    id=uuid.uuid4(),
                    audit_run_id=audit_run_id,
                    ord=ord_,
                    claim_text=extracted.claim_text,
                    citation_text=extracted.citation_text,
                    resolution=resolved.resolution,
                    openalex_id=match.openalex_id if match else None,
                    doi=match.doi if match else None,
                    matched_title=match.title if match else None,
                    matched_year=match.year if match else None,
                    matched_authors_json=(
                        [match.first_author] if match and match.first_author else None
                    ),
                    candidates_json=(
                        _candidate_payload(resolved.candidates)
                        if resolved.resolution == NEEDS_CONFIRMATION
                        else None
                    ),
                )

                # Only a deterministic or confirmed match is audited. Anything
                # awaiting confirmation stops here with its candidates attached.
                if resolved.resolution in AUDITABLE_RESOLUTIONS and match is not None:
                    quote, source = await select_evidence(
                        provider,
                        session,
                        audit_run.project_id,
                        extracted.claim_text,
                        match,
                        embed_model=settings.embed_model,
                    )
                    row.evidence_source = source
                    paper = await find_project_paper(session, audit_run.project_id, match)
                    row.paper_id = paper.id if paper else None
                else:
                    quote, source = None, None

                session.add(row)
                await session.flush()
                await redis.publish(
                    channel, json.dumps({"kind": "citation", "payload": _citation_event(row)})
                )

                if quote is None:
                    # Nothing to check this claim against. Deliberately no
                    # finding: the citation row already says why, and inventing
                    # an unsupported verdict here would accuse a citation that
                    # was never actually examined.
                    await session.commit()
                    continue

                checked = await audit_evidence(
                    checker=provider,
                    assertion=extracted.claim_text,
                    quotes=[quote],
                    checker_model_name=settings.checker_model,
                )
                quotes_checked += 1
                malformed_entries += checked.malformed_entries
                supported, rationale = checked.results[0]

                finding = AuditFinding(
                    id=uuid.uuid4(),
                    audit_run_id=audit_run_id,
                    ord=ord_counter,
                    draft_citation_id=row.id,
                    sub_id=str(ord_ + 1),
                    quote=quote,
                    supported=supported,
                    rationale=rationale,
                )
                session.add(finding)
                await session.flush()
                await redis.publish(channel, json.dumps({
                    "kind": "finding",
                    "ord": ord_counter,
                    "payload": {
                        "draft_citation_id": str(row.id),
                        "sub_id": finding.sub_id,
                        "quote": quote,
                        "evidence_source": source,
                        "supported": supported,
                        "rationale": rationale,
                    },
                }))
                ord_counter += 1
                await session.commit()

            audit_run.quotes_checked = quotes_checked
            audit_run.malformed_entries = malformed_entries
            await session.commit()

            rate = malformed_rate(malformed_entries, quotes_checked)
            log.info(
                "draft_audit.response_integrity",
                audit_run_id=str(audit_run_id),
                quotes_checked=quotes_checked,
                malformed_entries=malformed_entries,
                malformed_rate=rate,
            )
            if rate is not None and rate > settings.audit_malformed_threshold:
                raise RuntimeError(
                    f"draft audit aborted: {malformed_entries} of {quotes_checked} evidence "
                    f"checks returned an unusable verdict ({rate:.1%}), above the "
                    f"{settings.audit_malformed_threshold:.1%} threshold"
                )

            audit_run.status = "done"
            audit_run.model = settings.checker_model
            await session.commit()
            await redis.publish(channel, json.dumps({"kind": "done"}))
            log.info("draft_audit.done", audit_run_id=str(audit_run_id))

        except Exception as exc:
            log.exception("draft_audit.failed", audit_run_id=str(audit_run_id))
            await session.rollback()
            audit_run = await session.get(AuditRun, audit_run_id)
            if audit_run is not None:
                audit_run.status = "failed"
                await session.commit()
            await redis.publish(
                channel, json.dumps({"kind": "failed", "payload": {"error": str(exc)[:500]}})
            )
