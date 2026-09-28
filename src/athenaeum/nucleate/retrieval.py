"""The archivist's three retrieval sources, and merging what they return.

Sources, in the handoff's priority order: the user's library, OpenAlex, the
web. Each returns Candidates in one shape so the ranking stage can compare
them side by side, and each records where it came from — that becomes the
lineage edge's retrieval_source (D-N6).

Web results are pages, not papers, so each is matched to a scholarly work
before it can become a candidate — and only *deterministically* (D-N16): a DOI
in the page's URL or title, or a single exact title match. That is the same
rule the Audit tab uses for citations, for the same reason: a looser match
could attach a page's claims to a paper that never made them. Pages that match
nothing are dropped and counted, never guessed at.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.nucleate import LIBRARY, OPENALEX, WEB, RetrievalSource
from athenaeum.ingest.embed import embed_sentences
from athenaeum.ingest.openalex import OpenAlexSearchHit, search_works
from athenaeum.provider.anthropic_client import AnthropicProvider
from athenaeum.retrieve.hybrid import search as hybrid_search
from athenaeum.storage.models import Paper
from athenaeum.synthesize.draft_audit import find_doi, normalize_title, pick_exact_match
from athenaeum.synthesize.websearch_find import find_via_web_search

log = structlog.get_logger()


@dataclass
class Candidate:
    """One paper a source turned up, in a shape every source shares."""

    source: RetrievalSource
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    abstract: str | None = None
    # Set for library candidates, which are already Paper rows with full text.
    paper_id: uuid.UUID | None = None
    # For library candidates, the sentences that matched the query — the
    # ranking stage sees *why* the library turned this up.
    snippet: str | None = None
    cited_by_count: int = 0

    @property
    def key(self) -> str:
        """Identity for deduplication across sources: DOI, else OpenAlex id,
        else the normalized title."""
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        return f"title:{normalize_title(self.title)}"


def author_names(authors_json: Any) -> list[str]:
    """Names from a Paper's authors_json, which holds [{"name": ...}] from
    OpenAlex but plain strings from some older import paths."""
    if not isinstance(authors_json, list):
        return []
    names: list[str] = []
    for a in authors_json:
        if isinstance(a, dict):
            name = a.get("name") or a.get("display_name")
            if isinstance(name, str) and name:
                names.append(name)
        elif isinstance(a, str) and a:
            names.append(a)
    return names


def _from_hit(hit: OpenAlexSearchHit, source: RetrievalSource) -> Candidate:
    return Candidate(
        source=source,
        title=hit.title,
        authors=[hit.first_author] if hit.first_author else [],
        year=hit.year,
        venue=hit.venue,
        doi=hit.doi,
        openalex_id=hit.openalex_id,
        abstract=hit.abstract,
        cited_by_count=hit.cited_by_count,
    )


# ── Library ──────────────────────────────────────────────────────────────────


async def retrieve_library(
    session: AsyncSession,
    project_id: uuid.UUID,
    query: str,
    embed_model: str,
    top_k: int = 40,
) -> list[Candidate]:
    """Search the project's own ingested papers, grouping sentence hits by
    paper. Every library candidate has full text by construction."""
    if not query.strip():
        return []
    embedding = (await embed_sentences([query], embed_model))[0]
    hits = await hybrid_search(session, project_id, query, embedding, top_k=top_k)

    by_paper: dict[uuid.UUID, list[str]] = {}
    for hit in hits:
        by_paper.setdefault(hit.paper.id, []).append(hit.text)
    if not by_paper:
        return []

    papers = {
        p.id: p for p in await session.scalars(select(Paper).where(Paper.id.in_(by_paper)))
    }
    candidates: list[Candidate] = []
    for paper_id, sentences in by_paper.items():
        paper = papers.get(paper_id)
        if paper is None:
            continue
        candidates.append(Candidate(
            source=LIBRARY,
            title=paper.title,
            authors=author_names(paper.authors_json),
            year=paper.year,
            venue=paper.venue,
            doi=paper.doi,
            openalex_id=paper.openalex_id,
            abstract=paper.abstract,
            paper_id=paper.id,
            snippet=" … ".join(sentences[:3]),
        ))
    return candidates


# ── OpenAlex ─────────────────────────────────────────────────────────────────


@dataclass
class OpenAlexResult:
    candidates: list[Candidate]
    # Queries that failed even after retries. Counted so the stage can say
    # so: a failed query and a query with no hits must not look the same.
    failed_queries: int


async def retrieve_openalex(
    queries: list[str], per_query: int = 10, email: str = "contact@athenaeum.app"
) -> OpenAlexResult:
    """Run each scholarly query against OpenAlex. A query that fails is logged,
    counted and skipped rather than failing the whole expansion."""
    loop = asyncio.get_running_loop()
    candidates: list[Candidate] = []
    failed = 0
    for query in queries:
        if not query.strip():
            continue
        try:
            hits = await loop.run_in_executor(None, search_works, query, per_query, email)
        except Exception as exc:
            log.warning("archivist.openalex_failed", query=query, error=str(exc)[:200])
            failed += 1
            continue
        candidates.extend(_from_hit(h, OPENALEX) for h in hits)
    return OpenAlexResult(candidates=candidates, failed_queries=failed)


# ── Web ──────────────────────────────────────────────────────────────────────


@dataclass
class WebResult:
    candidates: list[Candidate]
    # Pages found that matched no scholarly work deterministically. Counted so
    # the stage can say so, rather than silently vanishing.
    unmatched: int


async def _match_page(
    url: str, title: str, email: str
) -> OpenAlexSearchHit | None:
    """Match one web page to a scholarly work, deterministically or not at all."""
    loop = asyncio.get_running_loop()
    doi = find_doi(url) or find_doi(title)
    try:
        if doi:
            hits = await loop.run_in_executor(None, search_works, doi, 5, email)
            return next((h for h in hits if h.doi and h.doi.lower() == doi), None)
        hits = await loop.run_in_executor(None, search_works, title, 8, email)
    except Exception as exc:
        log.warning("archivist.web_match_failed", url=url, error=str(exc)[:200])
        return None
    # A single work whose full title appears verbatim in the page title; the
    # helper refuses short titles and ambiguous double matches.
    return pick_exact_match(title, hits)


async def retrieve_web(
    provider: AnthropicProvider, query: str, email: str = "contact@athenaeum.app"
) -> WebResult:
    """Search the web, then keep only pages that resolve to a scholarly work."""
    if not query.strip():
        return WebResult(candidates=[], unmatched=0)
    try:
        matches = await find_via_web_search(provider, query)
    except Exception as exc:
        log.warning("archivist.web_search_failed", error=str(exc)[:200])
        return WebResult(candidates=[], unmatched=0)

    candidates: list[Candidate] = []
    unmatched = 0
    for match in matches:
        hit = await _match_page(match.url, match.title, email)
        if hit is None:
            unmatched += 1
            continue
        candidates.append(_from_hit(hit, WEB))
    return WebResult(candidates=candidates, unmatched=unmatched)


# ── Merging ──────────────────────────────────────────────────────────────────

# When sources disagree about one paper, the library record wins: it has the
# full text and an existing Paper row. Then OpenAlex, then web.
_SOURCE_PRIORITY: dict[str, int] = {LIBRARY: 0, OPENALEX: 1, WEB: 2}


def merge_candidates(*groups: list[Candidate]) -> list[Candidate]:
    """One candidate per paper across all sources, keeping the best-sourced
    record, in the order papers were first seen."""
    merged: dict[str, Candidate] = {}
    for group in groups:
        for candidate in group:
            existing = merged.get(candidate.key)
            if existing is None or (
                _SOURCE_PRIORITY[candidate.source] < _SOURCE_PRIORITY[existing.source]
            ):
                merged[candidate.key] = candidate
    return list(merged.values())
