"""Tests against a hand-built sqlite db matching Zotero's local schema shape —
no real Zotero install needed. Covers search/browse/collections/attachment
resolution purely against the sqlite reads in ingest/zotero_local.py."""
import sqlite3
from pathlib import Path

import pytest

from athenaeum.ingest.zotero import pick_best_attachment
from athenaeum.ingest.zotero_local import (
    LocalZoteroError,
    browse_local_items,
    get_local_item,
    list_local_attachments,
    list_local_collections,
    resolve_local_attachment_file,
    search_local_library,
)

_SCHEMA = """
CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE items (
    itemID INTEGER PRIMARY KEY, itemTypeID INTEGER, key TEXT, dateAdded TEXT
);
CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
CREATE TABLE creators (
    creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INTEGER
);
CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, orderIndex INTEGER);
CREATE TABLE collections (
    collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INTEGER, key TEXT
);
CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER);
CREATE TABLE itemAttachments (
    itemID INTEGER, parentItemID INTEGER, linkMode INTEGER, contentType TEXT, path TEXT
);
CREATE TABLE deletedItems (itemID INTEGER);
"""


def _build_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)

    conn.execute("INSERT INTO itemTypes VALUES (1, 'journalArticle')")
    conn.execute("INSERT INTO itemTypes VALUES (2, 'attachment')")
    conn.execute("INSERT INTO itemTypes VALUES (3, 'note')")

    conn.execute("INSERT INTO fields VALUES (1, 'title')")
    conn.execute("INSERT INTO fields VALUES (2, 'date')")
    conn.execute("INSERT INTO fields VALUES (3, 'DOI')")
    conn.execute("INSERT INTO fields VALUES (4, 'publicationTitle')")
    conn.execute("INSERT INTO fields VALUES (5, 'abstractNote')")

    # Item 1: a journal article with a synced-looking imported PDF attachment.
    conn.execute("INSERT INTO items VALUES (1, 1, 'ITEM0001', '2024-01-01 00:00:00')")
    conn.execute("INSERT INTO itemDataValues VALUES (1, 'Deep Sea Vents and Life')")
    conn.execute("INSERT INTO itemDataValues VALUES (2, '2024-03-15')")
    conn.execute("INSERT INTO itemDataValues VALUES (3, '10.1234/deepsea')")
    conn.execute("INSERT INTO itemDataValues VALUES (4, 'Journal of Geobiology')")
    conn.execute("INSERT INTO itemData VALUES (1, 1, 1)")
    conn.execute("INSERT INTO itemData VALUES (1, 2, 2)")
    conn.execute("INSERT INTO itemData VALUES (1, 3, 3)")
    conn.execute("INSERT INTO itemData VALUES (1, 4, 4)")
    conn.execute("INSERT INTO creators VALUES (1, 'Ada', 'Borton', 0)")
    conn.execute("INSERT INTO itemCreators VALUES (1, 1, 0)")

    conn.execute(
        "INSERT INTO items VALUES (2, 2, 'ATTACH001', '2024-01-01 00:00:01')"
    )
    conn.execute(
        "INSERT INTO itemAttachments VALUES (2, 1, 0, 'application/pdf', 'storage:paper.pdf')"
    )

    # Item 2: a second article, older, with a linked_file attachment (must be skipped).
    conn.execute("INSERT INTO items VALUES (3, 1, 'ITEM0002', '2023-01-01 00:00:00')")
    conn.execute("INSERT INTO itemDataValues VALUES (5, 'Hydrothermal Chemistry')")
    conn.execute("INSERT INTO itemData VALUES (3, 1, 5)")
    conn.execute("INSERT INTO creators VALUES (2, NULL, 'Zheng Lab', 1)")
    conn.execute("INSERT INTO itemCreators VALUES (3, 2, 0)")
    conn.execute("INSERT INTO items VALUES (4, 2, 'ATTACH002', '2023-01-01 00:00:01')")
    conn.execute(
        "INSERT INTO itemAttachments VALUES (4, 3, 2, 'application/pdf', '/Users/x/local.pdf')"
    )

    # A trashed item that must never surface.
    conn.execute("INSERT INTO items VALUES (5, 1, 'ITEM0003', '2025-01-01 00:00:00')")
    conn.execute("INSERT INTO itemDataValues VALUES (6, 'Should Not Appear')")
    conn.execute("INSERT INTO itemData VALUES (5, 1, 6)")
    conn.execute("INSERT INTO deletedItems VALUES (5)")

    # A collection containing only item 1.
    conn.execute("INSERT INTO collections VALUES (1, 'Vents', NULL, 'COLL0001')")
    conn.execute("INSERT INTO collectionItems VALUES (1, 1)")

    conn.commit()
    conn.close()


@pytest.fixture
def zotero_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "zotero-data"
    data_dir.mkdir()
    _build_db(data_dir / "zotero.sqlite")
    storage_dir = data_dir / "storage" / "ATTACH001"
    storage_dir.mkdir(parents=True)
    (storage_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
    return data_dir


async def test_search_matches_title(zotero_dir: Path) -> None:
    hits = await search_local_library(str(zotero_dir), "deep sea")
    assert [h.item_key for h in hits] == ["ITEM0001"]
    assert hits[0].doi == "10.1234/deepsea"
    assert hits[0].creators == ["Ada Borton"]


async def test_search_matches_creator(zotero_dir: Path) -> None:
    hits = await search_local_library(str(zotero_dir), "zheng")
    assert [h.item_key for h in hits] == ["ITEM0002"]


async def test_search_excludes_trashed_items(zotero_dir: Path) -> None:
    hits = await search_local_library(str(zotero_dir), "should not appear")
    assert hits == []


async def test_browse_all_sorted_by_date_added_desc(zotero_dir: Path) -> None:
    hits, total = await browse_local_items(str(zotero_dir), collection_key=None)
    assert total == 2
    assert [h.item_key for h in hits] == ["ITEM0001", "ITEM0002"]


async def test_browse_scoped_to_collection(zotero_dir: Path) -> None:
    hits, total = await browse_local_items(str(zotero_dir), collection_key="COLL0001")
    assert total == 1
    assert hits[0].item_key == "ITEM0001"


async def test_list_collections(zotero_dir: Path) -> None:
    collections = await list_local_collections(str(zotero_dir))
    assert len(collections) == 1
    assert collections[0].key == "COLL0001"
    assert collections[0].name == "Vents"
    assert collections[0].parent_key is None


async def test_get_local_item(zotero_dir: Path) -> None:
    data = await get_local_item(str(zotero_dir), "ITEM0001")
    assert data["title"] == "Deep Sea Vents and Life"
    assert data["DOI"] == "10.1234/deepsea"


async def test_get_local_item_missing_key_raises(zotero_dir: Path) -> None:
    with pytest.raises(LocalZoteroError):
        await get_local_item(str(zotero_dir), "NOPE")


async def test_list_attachments_resolves_synced_pdf(zotero_dir: Path) -> None:
    attachments = await list_local_attachments(str(zotero_dir), "ITEM0001")
    assert len(attachments) == 1
    att = attachments[0]
    assert att.link_mode == "imported_file"
    assert att.content_type == "application/pdf"
    assert att.synced is True
    assert att.filename == "paper.pdf"


async def test_list_attachments_linked_file_not_synced(zotero_dir: Path) -> None:
    attachments = await list_local_attachments(str(zotero_dir), "ITEM0002")
    assert len(attachments) == 1
    assert attachments[0].link_mode == "linked_file"
    assert attachments[0].synced is False


async def test_pick_best_attachment_reuses_shared_logic(zotero_dir: Path) -> None:
    attachments = await list_local_attachments(str(zotero_dir), "ITEM0001")
    best = pick_best_attachment(attachments)
    assert best is not None
    assert best.filename == "paper.pdf"


async def test_resolve_local_attachment_file(zotero_dir: Path) -> None:
    attachments = await list_local_attachments(str(zotero_dir), "ITEM0001")
    best = pick_best_attachment(attachments)
    assert best is not None
    resolved = resolve_local_attachment_file(str(zotero_dir), best)
    assert resolved is not None
    assert resolved.name == "paper.pdf"
    assert resolved.read_bytes() == b"%PDF-1.4 fake"


async def test_missing_data_dir_raises() -> None:
    with pytest.raises(LocalZoteroError):
        await list_local_collections("/nonexistent/path")
