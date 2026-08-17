"""Zotero Web API integration: credential checks, library search, item mapping,
and attachment selection/download.

Metadata/search calls go through `pyzotero` (sync, run in an executor) — the
same pattern used for OpenAlex via `pyalex`. Attachment *file* downloads are
hand-rolled async httpx instead: pyzotero's `.file()` buffers the whole
response in memory with no size cap, which is fine for pyalex's curated OA
PDF links but not for arbitrary user-uploaded Zotero attachments.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog
from pyzotero import (
    TooManyRequestsError,
    TooManyRetriesError,
    UnsupportedParamsError,
    UserNotAuthorisedError,
    Zotero,
)

log = structlog.get_logger()

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  # matches papers.py's upload cap

# Only these linkModes point at a file Zotero itself stores and can serve back
# over the API. `linked_file` points at a path on the user's own machine;
# `linked_url` points at an arbitrary external URL — neither is reachable (or
# safe to fetch) from the server.
_DOWNLOADABLE_LINK_MODES = {"imported_file", "imported_url"}


class ZoteroAuthError(RuntimeError):
    """The stored Zotero API key / user id is invalid, revoked, or unauthorized."""


class ZoteroRateLimitError(RuntimeError):
    """The Zotero API is rate-limiting this key."""


@dataclass
class ZoteroCreds:
    zotero_user_id: str
    api_key: str
    library_type: str = "user"


@dataclass
class ZoteroSearchHit:
    item_key: str
    title: str
    creators: list[str]
    year: int | None
    venue: str | None
    doi: str | None
    item_type: str
    date_added: str | None = None
    abstract: str | None = None


@dataclass
class ZoteroAttachment:
    item_key: str
    content_type: str
    link_mode: str
    filename: str | None
    synced: bool


@dataclass
class ZoteroKeyInfo:
    user_id: str
    username: str


@dataclass
class ZoteroCollection:
    key: str
    name: str
    parent_key: str | None


@dataclass
class ZoteroAnnotation:
    """A highlight/underline a user made in Zotero's own PDF reader — the
    only annotation types that carry verbatim text (note/image/ink don't).
    `text` is Zotero's own PDF-text-extraction of the highlighted span, not
    Athenaeum's — see ingest/highlight_match.py for why that means fuzzy,
    not exact, matching against Athenaeum's own sentence parse."""

    item_key: str
    parent_item_key: str
    annotation_type: str
    text: str
    comment: str | None
    color: str | None
    page_label: str | None


# Only these carry a highlighted text span (note/image/ink annotations don't).
_TEXT_ANNOTATION_TYPES = {"highlight", "underline"}


def _client(creds: ZoteroCreds) -> Zotero:
    return Zotero(creds.zotero_user_id, creds.library_type, creds.api_key)


def _run_zotero_call(fn):
    """Translate pyzotero's auth/rate-limit exceptions into our own types."""
    try:
        return fn()
    except UserNotAuthorisedError as exc:
        raise ZoteroAuthError(str(exc)) from exc
    except UnsupportedParamsError as exc:
        # Zotero validates API key *syntax* before authorization and returns a
        # plain 400 for a malformed key — treat it the same as bad credentials
        # rather than a generic upstream failure.
        raise ZoteroAuthError(str(exc)) from exc
    except (TooManyRequestsError, TooManyRetriesError) as exc:
        raise ZoteroRateLimitError(str(exc)) from exc


def _normalize_doi(doi: str) -> str:
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            return doi[len(prefix):]
    return doi


def _creator_names(data: dict) -> list[str]:
    names: list[str] = []
    for creator in data.get("creators", []) or []:
        name = creator.get("name") or " ".join(
            part for part in (creator.get("firstName"), creator.get("lastName")) if part
        )
        if name:
            names.append(name)
    return names


def _year_from_date(date_str: str | None) -> int | None:
    if not date_str:
        return None
    match = re.search(r"(19|20)\d{2}", date_str)
    return int(match.group(0)) if match else None


async def validate_api_key(api_key: str) -> ZoteroKeyInfo:
    """Validate a Zotero API key and return the numeric user ID it belongs to.

    Zotero's own `/keys/{api_key}` endpoint reports the account tied to the
    key — we derive the numeric user ID from *this* response rather than ever
    trusting a user-typed value. Zotero's website prominently shows a
    `username` (e.g. "tacaro", visible in your profile URL) that looks like it
    belongs in a "user ID" field but is a different identifier from the
    numeric ID the API actually requires in its URLs — asking a user to type
    the numeric ID separately is exactly what caused that mismatch.
    """
    loop = asyncio.get_running_loop()

    def _check() -> dict:
        # library_id/library_type are required by the client constructor but
        # are not used by the /keys endpoint itself — placeholders are fine.
        client = Zotero("0", "user", api_key)
        return client.key_info()

    info = await loop.run_in_executor(None, lambda: _run_zotero_call(_check))
    return ZoteroKeyInfo(user_id=str(info["userID"]), username=info.get("username", ""))


def _hit_from_item(data: dict) -> ZoteroSearchHit | None:
    """Map a Zotero item's `data` block to a ZoteroSearchHit, or None if it's
    a note/attachment (not a browsable/importable bibliographic item)."""
    item_type = data.get("itemType", "")
    if item_type in ("note", "attachment"):
        return None
    raw_doi = data.get("DOI") or None
    return ZoteroSearchHit(
        item_key=data.get("key", ""),
        title=data.get("title") or "(untitled)",
        creators=_creator_names(data),
        year=_year_from_date(data.get("date")),
        venue=data.get("publicationTitle") or data.get("websiteTitle") or None,
        doi=_normalize_doi(raw_doi) if raw_doi else None,
        item_type=item_type,
        date_added=data.get("dateAdded"),
        abstract=data.get("abstractNote") or None,
    )


async def search_library(creds: ZoteroCreds, query: str, limit: int = 20) -> list[ZoteroSearchHit]:
    """Quick-search the user's top-level library items by title/creator/year."""
    loop = asyncio.get_running_loop()

    def _search() -> list[dict]:
        client = _client(creds)
        return client.top(q=query, qmode="titleCreatorYear", limit=limit)

    items = await loop.run_in_executor(None, lambda: _run_zotero_call(_search))
    return [hit for item in items if (hit := _hit_from_item(item.get("data", {}))) is not None]


async def search_library_everything(
    creds: ZoteroCreds, query: str, limit: int = 40
) -> list[ZoteroSearchHit]:
    """Broader quick-search covering abstracts/notes/tags/full-text, not just
    title/creator/year — used by the "Find" library-wide discovery feature,
    which needs abstract matches that `search_library` (used by the add-paper
    tab) intentionally doesn't do."""
    loop = asyncio.get_running_loop()

    def _search() -> list[dict]:
        client = _client(creds)
        return client.top(q=query, qmode="everything", limit=limit)

    items = await loop.run_in_executor(None, lambda: _run_zotero_call(_search))
    return [hit for item in items if (hit := _hit_from_item(item.get("data", {}))) is not None]


# Our own sort-field vocabulary -> Zotero's `sort` query parameter values.
_SORT_FIELD_MAP = {
    "title": "title",
    "creator": "creator",
    "year": "date",
    "date_added": "dateAdded",
}


async def list_collections(creds: ZoteroCreds) -> list[ZoteroCollection]:
    """List every collection (Zotero's "folders") in the library, flat.

    Each collection reports its own parent (or none, for a top-level
    collection) — the caller assembles this flat list into a tree.
    """
    loop = asyncio.get_running_loop()

    def _list() -> list[dict]:
        client = _client(creds)
        return client.collections(limit=100)

    rows = await loop.run_in_executor(None, lambda: _run_zotero_call(_list))
    collections: list[ZoteroCollection] = []
    for row in rows:
        data = row.get("data", {})
        parent = data.get("parentCollection")
        collections.append(
            ZoteroCollection(
                key=data.get("key"),
                name=data.get("name") or "(untitled)",
                parent_key=parent if parent else None,
            )
        )
    return collections


async def browse_items(
    creds: ZoteroCreds,
    collection_key: str | None,
    start: int = 0,
    limit: int = 50,
    sort: str = "date_added",
    direction: str = "desc",
    query: str | None = None,
) -> tuple[list[ZoteroSearchHit], int]:
    """Paginated, sortable browse of library items, optionally scoped to a
    collection and/or filtered by a quick-search query.

    Returns (hits, total_count) — `total_count` reflects the current
    collection/query scope (from Zotero's `Total-Results` response header),
    for building "showing X-Y of Z" pagination controls.
    """
    loop = asyncio.get_running_loop()
    zotero_sort = _SORT_FIELD_MAP.get(sort, "dateAdded")

    def _browse() -> tuple[list[dict], int]:
        client = _client(creds)
        kwargs: dict = {"start": start, "limit": limit, "sort": zotero_sort, "direction": direction}
        if query:
            kwargs["q"] = query
            kwargs["qmode"] = "titleCreatorYear"
        if collection_key:
            items = client.collection_items(collection_key, **kwargs)
        else:
            items = client.top(**kwargs)
        if client.request is None:
            raise ZoteroRateLimitError("Zotero did not return a response object after browsing")
        total = int(client.request.headers.get("Total-Results", len(items)))
        return items, total

    items, total = await loop.run_in_executor(None, lambda: _run_zotero_call(_browse))
    hits = [hit for item in items if (hit := _hit_from_item(item.get("data", {}))) is not None]
    return hits, total


def zotero_item_to_paper_fields(data: dict) -> dict:
    """Pure mapping from a Zotero item's `data` block to Paper row fields."""
    creators = _creator_names(data)
    raw_doi = data.get("DOI") or None
    return {
        "title": data.get("title") or "(untitled)",
        "authors_json": {"names": creators} if creators else None,
        "venue": data.get("publicationTitle") or data.get("websiteTitle") or None,
        "year": _year_from_date(data.get("date")),
        "abstract": data.get("abstractNote") or None,
        "doi": _normalize_doi(raw_doi) if raw_doi else None,
    }


async def get_item(creds: ZoteroCreds, item_key: str) -> dict:
    """Fetch a single item's `data` block."""
    loop = asyncio.get_running_loop()

    def _get() -> dict:
        client = _client(creds)
        result = client.item(item_key)
        return result.get("data", {})

    return await loop.run_in_executor(None, lambda: _run_zotero_call(_get))


async def list_attachments(creds: ZoteroCreds, item_key: str) -> list[ZoteroAttachment]:
    loop = asyncio.get_running_loop()

    def _children() -> list[dict]:
        client = _client(creds)
        return client.children(item_key)

    children = await loop.run_in_executor(None, lambda: _run_zotero_call(_children))

    attachments: list[ZoteroAttachment] = []
    for child in children:
        data = child.get("data", {})
        if data.get("itemType") != "attachment":
            continue
        attachments.append(
            ZoteroAttachment(
                item_key=data.get("key"),
                content_type=data.get("contentType") or "",
                link_mode=data.get("linkMode") or "",
                filename=data.get("filename"),
                # Zotero syncs item *metadata* unconditionally, but attachment
                # *file content* only if the user's account has synced it to
                # Zotero's cloud storage (a separate, often-unused setting).
                # `md5` is only ever populated once the file itself has been
                # uploaded — an `imported_file`/`imported_url` attachment with
                # no md5 exists as a record, but the bytes live only on
                # whichever computer runs Zotero desktop, unreachable from here.
                synced=bool(data.get("md5")),
            )
        )
    return attachments


async def list_annotations(creds: ZoteroCreds, attachment_item_key: str) -> list[ZoteroAnnotation]:
    """Highlight/underline annotations on one attachment. Annotation
    *metadata* sync is a free-tier Zotero account feature, independent of
    the (paid) file-storage sync `pick_best_attachment` cares about — an
    attachment that was never itself downloadable can still have annotations
    reachable here."""
    loop = asyncio.get_running_loop()

    def _children() -> list[dict]:
        client = _client(creds)
        return client.children(attachment_item_key)

    children = await loop.run_in_executor(None, lambda: _run_zotero_call(_children))

    annotations: list[ZoteroAnnotation] = []
    for child in children:
        data = child.get("data", {})
        if data.get("itemType") != "annotation":
            continue
        annotation_type = data.get("annotationType") or ""
        if annotation_type not in _TEXT_ANNOTATION_TYPES:
            continue
        text = data.get("annotationText")
        if not text:
            continue
        annotations.append(
            ZoteroAnnotation(
                item_key=data.get("key", ""),
                parent_item_key=attachment_item_key,
                annotation_type=annotation_type,
                text=text,
                comment=data.get("annotationComment") or None,
                color=data.get("annotationColor"),
                page_label=data.get("annotationPageLabel"),
            )
        )
    return annotations


async def list_annotations_for_item(creds: ZoteroCreds, item_key: str) -> list[ZoteroAnnotation]:
    """Every highlight/underline annotation across all of a top-level item's
    attachments — the convenience most callers actually want."""
    attachments = await list_attachments(creds, item_key)
    if not attachments:
        return []
    results = await asyncio.gather(*(list_annotations(creds, a.item_key) for a in attachments))
    return [ann for group in results for ann in group]


def pick_best_attachment(attachments: list[ZoteroAttachment]) -> ZoteroAttachment | None:
    """Prefer a downloadable PDF, then a downloadable HTML snapshot.

    Skips `linked_file`/`linked_url` attachments (never reachable from the
    server) and any attachment whose file was never synced to Zotero's cloud
    storage (also never reachable from the server, despite otherwise looking
    like a normal imported attachment) — see field docs on `synced` above.
    """
    downloadable = [
        a for a in attachments if a.link_mode in _DOWNLOADABLE_LINK_MODES and a.synced
    ]
    for attachment in downloadable:
        if attachment.content_type == "application/pdf":
            return attachment
    for attachment in downloadable:
        if attachment.content_type == "text/html":
            return attachment
    return None


async def download_attachment(
    creds: ZoteroCreds,
    attachment: ZoteroAttachment,
    client: httpx.AsyncClient,
    dest: Path,
    api_base: str,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> None:
    """Stream an attachment's file content to `dest`, enforcing a hard size cap."""
    library_segment = f"{creds.library_type}s"  # 'user' -> 'users', 'group' -> 'groups'
    url = f"{api_base}/{library_segment}/{creds.zotero_user_id}/items/{attachment.item_key}/file"
    headers = {"Zotero-API-Key": creds.api_key}

    async with client.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=httpx.Timeout(10.0, read=120.0)
    ) as resp:
        if resp.status_code in (401, 403):
            raise ZoteroAuthError(f"Zotero returned {resp.status_code} fetching attachment")
        if resp.status_code == 429:
            raise ZoteroRateLimitError("Zotero rate limit exceeded fetching attachment")
        resp.raise_for_status()

        written = 0
        with dest.open("wb") as fh:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                written += len(chunk)
                if written > max_bytes:
                    dest.unlink(missing_ok=True)
                    raise ValueError(f"attachment exceeds max size of {max_bytes} bytes")
                fh.write(chunk)
