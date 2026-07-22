from athenaeum.ingest.zotero import ZoteroAttachment, pick_best_attachment


def _att(
    content_type: str, link_mode: str, key: str = "AAAA1111", synced: bool = True
) -> ZoteroAttachment:
    return ZoteroAttachment(
        item_key=key, content_type=content_type, link_mode=link_mode, filename=None, synced=synced
    )


def test_prefers_pdf_over_snapshot() -> None:
    attachments = [
        _att("text/html", "imported_url", key="SNAP"),
        _att("application/pdf", "imported_file", key="PDF"),
    ]
    best = pick_best_attachment(attachments)
    assert best is not None
    assert best.item_key == "PDF"


def test_falls_back_to_snapshot_when_no_pdf() -> None:
    attachments = [_att("text/html", "imported_url", key="SNAP")]
    best = pick_best_attachment(attachments)
    assert best is not None
    assert best.item_key == "SNAP"


def test_skips_linked_file() -> None:
    attachments = [_att("application/pdf", "linked_file", key="LOCAL")]
    assert pick_best_attachment(attachments) is None


def test_skips_linked_url() -> None:
    attachments = [_att("text/html", "linked_url", key="EXTERNAL")]
    assert pick_best_attachment(attachments) is None


def test_empty_list_returns_none() -> None:
    assert pick_best_attachment([]) is None


def test_ignores_other_content_types() -> None:
    attachments = [_att("application/epub+zip", "imported_file", key="EPUB")]
    assert pick_best_attachment(attachments) is None


def test_skips_imported_file_never_synced_to_zotero_cloud() -> None:
    """Regression test: an `imported_file` attachment with no md5 exists only
    on whichever computer runs Zotero desktop — the server can't fetch it,
    even though linkMode alone makes it look downloadable."""
    attachments = [_att("application/pdf", "imported_file", key="UNSYNCED", synced=False)]
    assert pick_best_attachment(attachments) is None


def test_falls_back_to_synced_snapshot_when_pdf_unsynced() -> None:
    attachments = [
        _att("application/pdf", "imported_file", key="PDF", synced=False),
        _att("text/html", "imported_url", key="SNAP", synced=True),
    ]
    best = pick_best_attachment(attachments)
    assert best is not None
    assert best.item_key == "SNAP"
