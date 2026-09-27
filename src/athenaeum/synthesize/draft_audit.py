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
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.draft_audit import (
    ABSTRACT,
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
from athenaeum.storage.models import Paper, ProjectPaper
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
