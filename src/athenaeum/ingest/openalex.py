"""Fetch paper metadata from the OpenAlex API via pyalex."""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pyalex
from pyalex import Works

_OPENALEX_WORKS_URL = "https://api.openalex.org/works"


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


def _authors_from_work(work: dict) -> list[dict]:
    return [
        {"name": a["author"].get("display_name"), "orcid": a["author"].get("orcid")}
        for a in (work.get("authorships") or [])
    ]


def _meta_from_work(work: dict, *, fallback_doi: str | None = None) -> OpenAlexMeta:
    """Map a raw OpenAlex work dict (from pyalex or a raw REST response) into
    OpenAlexMeta — shared by fetch_metadata, fetch_metadata_by_id, and
    fetch_works_by_ids so the field derivations stay in one place."""
    raw_doi = work.get("doi")
    doi = _normalize_doi(raw_doi) if raw_doi else fallback_doi
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    best_oa = work.get("best_oa_location") or {}

    return OpenAlexMeta(
        openalex_id=work.get("id"),
        doi=doi,
        title=work.get("title") or (f"[Unknown] {fallback_doi}" if fallback_doi else "[Untitled]"),
        authors=_authors_from_work(work),
        venue=source.get("display_name"),
        year=work.get("publication_year"),
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
        oa_pdf_url=best_oa.get("pdf_url"),
    )


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

    return _meta_from_work(work, fallback_doi=bare_doi)


def fetch_metadata_by_id(
    openalex_id: str, email: str = "contact@athenaeum.app",
) -> OpenAlexMeta | None:
    """Single-work lookup by OpenAlex ID — used once a specific reference has
    been identified (by identify_cited_work), to get its full abstract +
    open-access PDF URL (deliberately not fetched in bulk by
    fetch_works_by_ids, to keep that cheap for papers with many references)."""
    pyalex.config.email = email
    try:
        work = Works()[openalex_id]
    except Exception:
        return None
    return _meta_from_work(work)


def fetch_works_by_ids(ids: list[str], email: str = "contact@athenaeum.app") -> list[OpenAlexMeta]:
    """Batched title/author/year lookup for a paper's own referenced_works.
    Abstracts ARE included (OpenAlex returns them for free in the same
    response) but callers doing a first-pass candidate scan can ignore them —
    only fetch_metadata_by_id's single result is used for abstract text, to
    keep prompts small when a paper has dozens of references.

    Synchronous — run in a thread executor from async context. Uses a raw
    REST call (not pyalex's Works().filter(...)) since a multi-ID OR filter
    isn't one of pyalex's convenience methods; OpenAlex allows up to 50 ids
    per request via `filter=ids.openalex:ID1|ID2|...`, so this batches.
    """
    if not ids:
        return []
    results: list[OpenAlexMeta] = []
    with httpx.Client(timeout=15.0) as client:
        for i in range(0, len(ids), 50):
            batch = ids[i : i + 50]
            resp = client.get(
                _OPENALEX_WORKS_URL,
                params={
                    "filter": "ids.openalex:" + "|".join(batch),
                    "per-page": len(batch),
                    "mailto": email,
                },
            )
            resp.raise_for_status()
            results.extend(_meta_from_work(w) for w in resp.json().get("results", []))
    return results
