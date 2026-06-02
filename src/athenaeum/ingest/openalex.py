"""Fetch paper metadata from the OpenAlex API via pyalex."""
from __future__ import annotations

from dataclasses import dataclass, field

import pyalex
from pyalex import Works


@dataclass
class OpenAlexMeta:
    openalex_id: str | None
    doi: str | None
    title: str
    authors: list[dict]
    venue: str | None
    year: int | None
    abstract: str | None
    oa_pdf_url: str | None


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    max_pos = max(pos for positions in inverted_index.values() for pos in positions)
    words: list[str] = [""] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(w for w in words if w)


def _normalize_doi(doi: str) -> str:
    """Strip any URL prefix so we have a bare DOI."""
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            return doi[len(prefix):]
    return doi


@dataclass
class OpenAlexSearchHit:
    openalex_id: str | None
    doi: str | None
    title: str
    first_author: str | None
    venue: str | None
    year: int | None
    abstract: str | None
    cited_by_count: int


def search_works(
    query: str,
    per_page: int = 15,
    email: str = "contact@athenaeum.app",
) -> list[OpenAlexSearchHit]:
    """Synchronous — run in a thread executor from async context."""
    pyalex.config.email = email
    works = Works().search(query).get(per_page=per_page)
    hits: list[OpenAlexSearchHit] = []
    for work in works:
        raw_doi = work.get("doi")
        doi = _normalize_doi(raw_doi) if raw_doi else None
        openalex_id: str | None = work.get("id")
        title: str = work.get("title") or ""
        year: int | None = work.get("publication_year")
        cited_by_count: int = work.get("cited_by_count") or 0

        authorships = work.get("authorships") or []
        first_author: str | None = None
        if authorships:
            first_author = (authorships[0].get("author") or {}).get("display_name")

        primary = work.get("primary_location") or {}
        source = primary.get("source") or {}
        venue: str | None = source.get("display_name")

        abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

        hits.append(OpenAlexSearchHit(
            openalex_id=openalex_id,
            doi=doi,
            title=title,
            first_author=first_author,
            venue=venue,
            year=year,
            abstract=abstract,
            cited_by_count=cited_by_count,
        ))
    return hits


def fetch_referenced_work_ids(openalex_id: str) -> list[str]:
    """Return OpenAlex work IDs referenced by the given work. Synchronous — run in executor."""
    try:
        work = Works()[openalex_id]
        return work.get("referenced_works") or []
    except Exception:
        return []


def fetch_metadata(doi: str, email: str = "contact@athenaeum.app") -> OpenAlexMeta:
    """Synchronous call — run in a thread executor if called from async context."""
    pyalex.config.email = email
    bare_doi = _normalize_doi(doi)

    works = Works().filter(doi=bare_doi).get()
    if not works:
        # Fallback: direct lookup by URL
        try:
            work = Works()[f"https://doi.org/{bare_doi}"]
        except Exception:
            return OpenAlexMeta(
                openalex_id=None,
                doi=bare_doi,
                title=f"[Unknown] {bare_doi}",
                authors=[],
                venue=None,
                year=None,
                abstract=None,
                oa_pdf_url=None,
            )
    else:
        work = works[0]

    openalex_id: str | None = work.get("id")
    title: str = work.get("title") or f"[Unknown] {bare_doi}"

    authors = [
        {
            "name": a["author"].get("display_name"),
            "orcid": a["author"].get("orcid"),
        }
        for a in (work.get("authorships") or [])
    ]

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    venue: str | None = source.get("display_name")

    year: int | None = work.get("publication_year")

    abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))

    best_oa = work.get("best_oa_location") or {}
    oa_pdf_url: str | None = best_oa.get("pdf_url")

    return OpenAlexMeta(
        openalex_id=openalex_id,
        doi=bare_doi,
        title=title,
        authors=authors,
        venue=venue,
        year=year,
        abstract=abstract,
        oa_pdf_url=oa_pdf_url,
    )
