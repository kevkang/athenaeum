"""Tests for capturing a Zotero annotation's position at highlight import.

Two concerns. First, parsing: Zotero hands position over as a JSON string on
both the Web API and local-sqlite paths, and a value it cannot parse must not
cost the user the import. Second, provenance: the import takes position from a
server-side re-read of the annotation rather than from the request body, so
these tests drive the endpoint function with a stubbed lookup and assert what
lands on the row.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException, status

from athenaeum.api import zotero_highlights
from athenaeum.domain.models import ZoteroHighlightImportItem, ZoteroHighlightImportRequest
from athenaeum.ingest.zotero import ZoteroAnnotation, parse_annotation_position
from athenaeum.storage.models import (
    Highlight,
    Paper,
    PaperSection,
    PaperSentence,
    Project,
    ProjectMember,
    User,
)

_POSITION = {"pageIndex": 6, "rects": [[71.0, 512.2, 520.8, 524.6]]}


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_parses_zoteros_json_string() -> None:
    raw = '{"pageIndex": 6, "rects": [[71.0, 512.2, 520.8, 524.6]]}'

    assert parse_annotation_position(raw) == _POSITION


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not json at all",
        '{"pageIndex": 6,',   # truncated
        "[1, 2, 3]",          # valid JSON, but not an object
        '"just a string"',
    ],
)
def test_unreadable_position_degrades_to_none(raw: str | None) -> None:
    """A highlight is worth importing for its text alone, so anything
    unparseable becomes 'no position' rather than an error."""
    assert parse_annotation_position(raw) is None


# ── Import ───────────────────────────────────────────────────────────────────

async def _seed(session) -> tuple[User, Project, Paper, PaperSentence]:
    """A project the user owns, holding one Zotero-imported paper with one
    sentence for the annotation to match against."""
    user = User(id=uuid.uuid4(), orcid="0000-0000-0000-0042", display_name="Position Tester")
    session.add(user)
    await session.flush()

    project = Project(id=uuid.uuid4(), title="Position Capture", owner_id=user.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))

    paper = Paper(
        id=uuid.uuid4(), title="Annotated Paper", year=2021, status="full_text",
        zotero_item_key="ITEMPOS01", zotero_source="local",
    )
    session.add(paper)
    await session.flush()

    section = PaperSection(
        id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0, char_start=0, char_end=1
    )
    session.add(section)
    await session.flush()

    sentence = PaperSentence(
        id=uuid.uuid4(), paper_id=paper.id, section_id=section.id, ord=0,
        text="Growth rates varied by two orders of magnitude.", char_start=0, char_end=47,
    )
    session.add(sentence)
    await session.flush()
    return user, project, paper, sentence


def _annotation(item_key: str = "ANNOTPOS1") -> ZoteroAnnotation:
    return ZoteroAnnotation(
        item_key=item_key, parent_item_key="ATTACHPOS", annotation_type="highlight",
        text="Growth rates varied by two orders of magnitude.", comment=None,
        color="#ffd400", page_label="S7", position=dict(_POSITION),
        sort_index="00006|000933|00512",
    )


def _request(project_id: uuid.UUID, sentence_id: uuid.UUID, key: str = "ANNOTPOS1"):
    return ZoteroHighlightImportRequest(
        project_id=project_id,
        items=[
            ZoteroHighlightImportItem(
                sentence_ids=[sentence_id],
                verbatim_text="Growth rates varied by two orders of magnitude.",
                annotation_item_key=key,
            )
        ],
    )


async def test_import_stores_position_page_and_sort_index(db_session, monkeypatch) -> None:
    user, project, paper, sentence = await _seed(db_session)
    monkeypatch.setattr(
        zotero_highlights, "_fetch_annotations",
        lambda session, user_id, p: _async([_annotation()]),
    )

    await zotero_highlights.import_zotero_highlights(
        paper.id, _request(project.id, sentence.id), db_session, user
    )

    row = await _only_highlight(db_session, project.id)
    assert row.zotero_annotation_key == "ANNOTPOS1"
    assert row.zotero_position_json == _POSITION
    assert row.zotero_sort_index == "00006|000933|00512"
    # The printed page label, which is not position's zero-based pageIndex.
    assert row.zotero_page_label == "S7"


async def test_import_ignores_position_a_client_tries_to_supply(db_session, monkeypatch) -> None:
    """Position is provenance, so it comes from the annotation the server read,
    never from the request. A client cannot assert where a highlight was."""
    user, project, paper, sentence = await _seed(db_session)
    monkeypatch.setattr(
        zotero_highlights, "_fetch_annotations",
        lambda session, user_id, p: _async([_annotation()]),
    )
    request = _request(project.id, sentence.id)
    # An extra field on the wire is not part of the schema and cannot reach the row.
    assert not hasattr(request.items[0], "position")

    await zotero_highlights.import_zotero_highlights(paper.id, request, db_session, user)

    row = await _only_highlight(db_session, project.id)
    assert row.zotero_position_json == _POSITION


async def test_import_still_succeeds_when_zotero_is_unreachable(db_session, monkeypatch) -> None:
    """A failed lookup must not cost the user the import. The annotation key is
    recorded regardless, which is what leaves the row backfillable later."""
    def _boom(session, user_id, p):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Zotero unreachable")

    user, project, paper, sentence = await _seed(db_session)
    monkeypatch.setattr(zotero_highlights, "_fetch_annotations", _boom)

    created = await zotero_highlights.import_zotero_highlights(
        paper.id, _request(project.id, sentence.id), db_session, user
    )

    assert len(created) == 1
    row = await _only_highlight(db_session, project.id)
    assert row.zotero_annotation_key == "ANNOTPOS1"
    assert row.zotero_position_json is None
    assert row.zotero_sort_index is None


async def test_import_records_key_when_annotation_has_since_been_deleted(
    db_session, monkeypatch
) -> None:
    """Zotero answered, but the annotation the preview offered is gone from it.
    The highlight imports on its matched text with no position."""
    user, project, paper, sentence = await _seed(db_session)
    monkeypatch.setattr(
        zotero_highlights, "_fetch_annotations",
        lambda session, user_id, p: _async([_annotation("SOMEOTHERKEY")]),
    )

    await zotero_highlights.import_zotero_highlights(
        paper.id, _request(project.id, sentence.id), db_session, user
    )

    row = await _only_highlight(db_session, project.id)
    assert row.zotero_annotation_key == "ANNOTPOS1"
    assert row.zotero_position_json is None


async def test_import_of_a_non_zotero_paper_skips_the_lookup(db_session, monkeypatch) -> None:
    """Nothing to look position up in, and no Zotero call should be attempted."""
    user, project, paper, sentence = await _seed(db_session)
    paper.zotero_item_key = None
    paper.zotero_source = None
    await db_session.flush()

    def _never(session, user_id, p):  # pragma: no cover - asserted not to run
        raise AssertionError("should not contact Zotero for a non-Zotero paper")

    monkeypatch.setattr(zotero_highlights, "_fetch_annotations", _never)

    await zotero_highlights.import_zotero_highlights(
        paper.id, _request(project.id, sentence.id), db_session, user
    )

    row = await _only_highlight(db_session, project.id)
    assert row.zotero_position_json is None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _async(value):
    """Wrap a value so a plain lambda can stand in for an async function."""
    return value


async def _only_highlight(session, project_id: uuid.UUID) -> Highlight:
    from sqlalchemy import select

    rows = list(
        await session.scalars(select(Highlight).where(Highlight.project_id == project_id))
    )
    assert len(rows) == 1
    return rows[0]
