"""Local Zotero library integration: read a user's own `zotero.sqlite` directly
(read-only) instead of going through the Zotero Web API.

This targets the schema used by Zotero 6/7 desktop. Zotero's local database is
undocumented and has shifted shape across versions before — if a future
version renames one of these tables/columns, these queries will need
updating; that's expected drift, not something a workaround here can prevent.

Metadata is stored as Zotero's own item-attribute-value tables (an EAV
layout), which is why several queries here build one item -> {field: value}
dict per candidate item rather than a single flat SELECT. Attachment *files*
already live on disk locally (no HTTP download needed) — we resolve
Zotero's `storage:<filename>` convention against `<data_dir>/storage/<attachment
key>/`.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.ingest.zotero import (
    ZoteroAttachment,
    ZoteroCollection,
    ZoteroSearchHit,
    _hit_from_item,
    _year_from_date,
)
from athenaeum.storage.models import ZoteroLocalConfig

log = structlog.get_logger()

_EXCLUDED_ITEM_TYPES = ("attachment", "note", "annotation")

# Zotero.Attachments.LINK_MODE_* constants (stable across recent Zotero versions).
_LINK_MODE_NAMES = {
    0: "imported_file",
    1: "imported_url",
    2: "linked_file",
    3: "linked_url",
    4: "embedded_image",
}


class LocalZoteroError(RuntimeError):
    """The local Zotero data directory is missing, unreadable, or malformed."""


def _db_path(data_dir: str) -> Path:
    path = Path(data_dir) / "zotero.sqlite"
    if not path.is_file():
        raise LocalZoteroError(f"No zotero.sqlite found in {data_dir}")
    return path


async def resolve_configured_data_dir(session: AsyncSession, host_mount: str | None) -> str:
    """Resolves the operator's chosen Zotero data directory (persisted in
    zotero_local_config, relative to host_mount) to an absolute path. Shared
    by the API layer (which wraps LocalZoteroError as an HTTPException) and
    the ingest pipeline (which doesn't have an HTTP request to attach one to)."""
    if not host_mount:
        raise LocalZoteroError("ZOTERO_LOCAL_HOST_MOUNT not configured")
    config = await session.get(ZoteroLocalConfig, 1)
    if config is None:
        raise LocalZoteroError("No local Zotero library selected yet")

    mount_root = Path(host_mount).resolve()
    candidate = (mount_root / config.relative_path).resolve()
    if candidate != mount_root and mount_root not in candidate.parents:
        raise LocalZoteroError("Configured local Zotero path escapes the mounted directory")
    if not (candidate / "zotero.sqlite").is_file():
        raise LocalZoteroError("Configured local Zotero path no longer contains a zotero.sqlite")
    return str(candidate)


def _connect(data_dir: str) -> sqlite3.Connection:
    path = _db_path(data_dir)
    # Immutable + read-only URI mode: Zotero itself holds this file open (and
    # in WAL mode) whenever it's running, so we never want to attempt a write
    # lock. `immutable=1` also lets sqlite skip the usual lock-file dance
    # entirely, which matters since Zotero may be running concurrently.
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    except sqlite3.OperationalError as exc:
        raise LocalZoteroError(f"Could not open {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _item_data_dict(conn: sqlite3.Connection, item_id: int) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT f.fieldName AS field, idv.value AS value
        FROM itemData id
        JOIN fields f ON f.fieldID = id.fieldID
        JOIN itemDataValues idv ON idv.valueID = id.valueID
        WHERE id.itemID = ?
        """,
        (item_id,),
    ).fetchall()
    return {row["field"]: row["value"] for row in rows}


def _item_creators(conn: sqlite3.Connection, item_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.firstName AS firstName, c.lastName AS lastName, c.fieldMode AS fieldMode
        FROM itemCreators ic
        JOIN creators c ON c.creatorID = ic.creatorID
        WHERE ic.itemID = ?
        ORDER BY ic.orderIndex
        """,
        (item_id,),
    ).fetchall()
    creators = []
    for row in rows:
        if row["fieldMode"] == 1:
            # Single-field name mode: the whole name is stored in lastName.
            creators.append({"name": row["lastName"]})
        else:
            creators.append({"firstName": row["firstName"], "lastName": row["lastName"]})
    return creators


def _row_to_data_block(conn: sqlite3.Connection, item_id: int, key: str, item_type: str) -> dict:
    """Builds a dict shaped like the Zotero Web API's item `data` block, so it
    can be fed straight into `zotero_item_to_paper_fields()` / `_hit_from_item()`
    without a second, parallel mapping implementation."""
    fields = _item_data_dict(conn, item_id)
    return {
        "key": key,
        "itemType": item_type,
        "title": fields.get("title"),
        "date": fields.get("date"),
        "publicationTitle": fields.get("publicationTitle"),
        "websiteTitle": fields.get("websiteTitle"),
        "abstractNote": fields.get("abstractNote"),
        "DOI": fields.get("DOI"),
        "creators": _item_creators(conn, item_id),
    }


def _candidate_items(
    conn: sqlite3.Connection, collection_key: str | None
) -> list[sqlite3.Row]:
    params: list = []
    collection_clause = ""
    if collection_key is not None:
        collection_clause = """
            AND i.itemID IN (
                SELECT ci.itemID FROM collectionItems ci
                JOIN collections col ON col.collectionID = ci.collectionID
                WHERE col.key = ?
            )
        """
        params.append(collection_key)

    placeholders = ",".join("?" for _ in _EXCLUDED_ITEM_TYPES)
    rows = conn.execute(
        f"""
        SELECT i.itemID AS itemID, i.key AS key, i.dateAdded AS dateAdded, it.typeName AS itemType
        FROM items i
        JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
        WHERE it.typeName NOT IN ({placeholders})
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
          {collection_clause}
        """,
        (*_EXCLUDED_ITEM_TYPES, *params),
    ).fetchall()
    return rows


def _matches_query(conn: sqlite3.Connection, item_id: int, query_lc: str) -> bool:
    title_row = conn.execute(
        """
        SELECT idv.value AS value
        FROM itemData id
        JOIN fields f ON f.fieldID = id.fieldID
        JOIN itemDataValues idv ON idv.valueID = id.valueID
        WHERE id.itemID = ? AND f.fieldName IN ('title', 'shortTitle')
        """,
        (item_id,),
    ).fetchall()
    if any(query_lc in (row["value"] or "").lower() for row in title_row):
        return True
    creator_row = conn.execute(
        """
        SELECT c.firstName AS firstName, c.lastName AS lastName
        FROM itemCreators ic
        JOIN creators c ON c.creatorID = ic.creatorID
        WHERE ic.itemID = ?
        """,
        (item_id,),
    ).fetchall()
    return any(
        query_lc in (row["firstName"] or "").lower() or query_lc in (row["lastName"] or "").lower()
        for row in creator_row
    )


def _matches_query_broad(conn: sqlite3.Connection, item_id: int, query_lc: str) -> bool:
    """Like `_matches_query`, but also checks the abstract — used only by the
    "Find" library-wide discovery feature. `browse_local_items`'s existing
    title/creator-only filtering is left untouched to avoid changing today's
    add-paper search behavior."""
    fields = _item_data_dict(conn, item_id)
    title = fields.get("title") or fields.get("shortTitle") or ""
    abstract = fields.get("abstractNote") or ""
    if query_lc in title.lower() or query_lc in abstract.lower():
        return True
    for creator in _item_creators(conn, item_id):
        name = creator.get("name") or f"{creator.get('firstName', '')} {creator.get('lastName', '')}"
        if query_lc in name.lower():
            return True
    return False


def _sort_key(conn: sqlite3.Connection, row: sqlite3.Row, sort: str) -> str:
    if sort == "date_added":
        return row["dateAdded"] or ""
    fields = _item_data_dict(conn, row["itemID"])
    if sort == "title":
        return (fields.get("title") or "").lower()
    if sort == "year":
        return f"{_year_from_date(fields.get('date')) or 0:06d}"
    if sort == "creator":
        creators = _item_creators(conn, row["itemID"])
        first = creators[0] if creators else {}
        return (first.get("lastName") or first.get("name") or "").lower()
    return row["dateAdded"] or ""


def _run(data_dir: str, fn):
    def _call():
        conn = _connect(data_dir)
        try:
            return fn(conn)
        finally:
            conn.close()

    return _call


async def search_local_library(
    data_dir: str, query: str, limit: int = 20
) -> list[ZoteroSearchHit]:
    loop = asyncio.get_running_loop()

    def _search(conn: sqlite3.Connection) -> list[ZoteroSearchHit]:
        query_lc = query.lower()
        candidates = _candidate_items(conn, collection_key=None)
        hits: list[ZoteroSearchHit] = []
        for row in candidates:
            if not _matches_query(conn, row["itemID"], query_lc):
                continue
            data = _row_to_data_block(conn, row["itemID"], row["key"], row["itemType"])
            data["dateAdded"] = row["dateAdded"]
            hit = _hit_from_item(data)
            if hit is not None:
                hits.append(hit)
            if len(hits) >= limit:
                break
        return hits

    return await loop.run_in_executor(None, _run(data_dir, _search))


async def search_local_library_broad(
    data_dir: str, query: str, limit: int = 40
) -> list[ZoteroSearchHit]:
    """Broader local-library search covering the abstract too, not just
    title/creator — used by the "Find" library-wide discovery feature."""
    loop = asyncio.get_running_loop()

    def _search(conn: sqlite3.Connection) -> list[ZoteroSearchHit]:
        query_lc = query.lower()
        candidates = _candidate_items(conn, collection_key=None)
        hits: list[ZoteroSearchHit] = []
        for row in candidates:
            if not _matches_query_broad(conn, row["itemID"], query_lc):
                continue
            data = _row_to_data_block(conn, row["itemID"], row["key"], row["itemType"])
            data["dateAdded"] = row["dateAdded"]
            hit = _hit_from_item(data)
            if hit is not None:
                hits.append(hit)
            if len(hits) >= limit:
                break
        return hits

    return await loop.run_in_executor(None, _run(data_dir, _search))


async def list_local_collections(data_dir: str) -> list[ZoteroCollection]:
    loop = asyncio.get_running_loop()

    def _list(conn: sqlite3.Connection) -> list[ZoteroCollection]:
        rows = conn.execute(
            """
            SELECT c.key AS key, c.collectionName AS name, parent.key AS parentKey
            FROM collections c
            LEFT JOIN collections parent ON parent.collectionID = c.parentCollectionID
            """
        ).fetchall()
        return [
            ZoteroCollection(
                key=row["key"], name=row["name"] or "(untitled)", parent_key=row["parentKey"]
            )
            for row in rows
        ]

    return await loop.run_in_executor(None, _run(data_dir, _list))


async def browse_local_items(
    data_dir: str,
    collection_key: str | None,
    start: int = 0,
    limit: int = 50,
    sort: str = "date_added",
    direction: str = "desc",
    query: str | None = None,
) -> tuple[list[ZoteroSearchHit], int]:
    loop = asyncio.get_running_loop()

    def _browse(conn: sqlite3.Connection) -> tuple[list[ZoteroSearchHit], int]:
        candidates = _candidate_items(conn, collection_key)
        if query:
            query_lc = query.lower()
            candidates = [r for r in candidates if _matches_query(conn, r["itemID"], query_lc)]

        candidates.sort(key=lambda r: _sort_key(conn, r, sort), reverse=(direction == "desc"))
        total = len(candidates)
        page = candidates[start : start + limit]

        hits: list[ZoteroSearchHit] = []
        for row in page:
            data = _row_to_data_block(conn, row["itemID"], row["key"], row["itemType"])
            data["dateAdded"] = row["dateAdded"]
            hit = _hit_from_item(data)
            if hit is not None:
                hits.append(hit)
        return hits, total

    return await loop.run_in_executor(None, _run(data_dir, _browse))


async def get_local_item(data_dir: str, item_key: str) -> dict:
    """Fetch a single item's data block, matching `get_item()`'s shape."""
    loop = asyncio.get_running_loop()

    def _get(conn: sqlite3.Connection) -> dict:
        row = conn.execute(
            """
            SELECT i.itemID AS itemID, i.key AS key, it.typeName AS itemType
            FROM items i
            JOIN itemTypes it ON it.itemTypeID = i.itemTypeID
            WHERE i.key = ?
            """,
            (item_key,),
        ).fetchone()
        if row is None:
            raise LocalZoteroError(f"No item with key {item_key!r}")
        return _row_to_data_block(conn, row["itemID"], row["key"], row["itemType"])

    return await loop.run_in_executor(None, _run(data_dir, _get))


def _resolve_attachment_path(
    data_dir: str, attachment_key: str, path_field: str | None
) -> Path | None:
    storage_dir = Path(data_dir) / "storage" / attachment_key
    if not storage_dir.is_dir():
        return None
    if path_field:
        has_prefix = path_field.startswith("storage:")
        filename = path_field[len("storage:") :] if has_prefix else path_field
        candidate = storage_dir / filename
        if candidate.is_file():
            return candidate
    # DB filename didn't match what's actually on disk (renamed after import,
    # or schema quirk) — fall back to whatever single file lives in the
    # attachment's own storage folder.
    files = [f for f in storage_dir.iterdir() if f.is_file()]
    return files[0] if len(files) == 1 else None


async def list_local_attachments(data_dir: str, item_key: str) -> list[ZoteroAttachment]:
    loop = asyncio.get_running_loop()

    def _list(conn: sqlite3.Connection) -> list[ZoteroAttachment]:
        parent_row = conn.execute("SELECT itemID FROM items WHERE key = ?", (item_key,)).fetchone()
        if parent_row is None:
            raise LocalZoteroError(f"No item with key {item_key!r}")

        rows = conn.execute(
            """
            SELECT i.key AS key, ia.linkMode AS linkMode, ia.contentType AS contentType,
                   ia.path AS path
            FROM itemAttachments ia
            JOIN items i ON i.itemID = ia.itemID
            WHERE ia.parentItemID = ?
              AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            """,
            (parent_row["itemID"],),
        ).fetchall()

        attachments: list[ZoteroAttachment] = []
        for row in rows:
            link_mode = _LINK_MODE_NAMES.get(row["linkMode"], "")
            resolved = None
            if link_mode in ("imported_file", "imported_url"):
                resolved = _resolve_attachment_path(data_dir, row["key"], row["path"])
            attachments.append(
                ZoteroAttachment(
                    item_key=row["key"],
                    content_type=row["contentType"] or "",
                    link_mode=link_mode,
                    filename=resolved.name if resolved else None,
                    # "synced" here means "a real file was found on disk" —
                    # there is no cloud-sync concept for a local-only read.
                    synced=resolved is not None,
                )
            )
        return attachments

    return await loop.run_in_executor(None, _run(data_dir, _list))


def resolve_local_attachment_file(
    data_dir: str, attachment: ZoteroAttachment
) -> Path | None:
    """Re-resolves an attachment's on-disk path (cheap enough to redo rather
    than carry a Path through the dataclass, which is API-response-shaped)."""
    conn = _connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT path FROM itemAttachments
            WHERE itemID = (SELECT itemID FROM items WHERE key = ?)
            """,
            (attachment.item_key,),
        ).fetchone()
        if row is None:
            return None
        return _resolve_attachment_path(data_dir, attachment.item_key, row["path"])
    finally:
        conn.close()
