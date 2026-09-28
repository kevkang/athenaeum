"""Regression tests for the JSON-null pitfall fixed by migration 0033.

SQLAlchemy's JSON types store Python None as the JSON value `null` unless told
otherwise, and `'null'::jsonb IS NULL` is false. That made "absent" invisible to
IS NULL queries — including the one a Zotero position backfill would use — and
let an unanchored quote through the selector CHECKs. These pin both fixes: the
columns store real SQL NULL, and the CHECKs refuse JSON null outright.
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from athenaeum.storage.models import (
    AuditDraftCitation,
    Highlight,
    Passage,
)

# Every JSONB column where NULL means "absent" (the list 0033 converts).
_ABSENT_MEANS_NULL = [
    (Passage, "text_quote_selector"),
    (Highlight, "text_quote_selector"),
    (Highlight, "zotero_position_json"),
    (AuditDraftCitation, "candidates_json"),
    (AuditDraftCitation, "matched_authors_json"),
]


@pytest.mark.parametrize(("model", "column"), _ABSENT_MEANS_NULL)
def test_none_is_declared_to_persist_as_sql_null(model, column) -> None:
    """Checked on the column definition itself, so a column added later without
    none_as_null=True is caught even before anything writes to it."""
    assert model.__table__.c[column].type.none_as_null is True, (
        f"{model.__tablename__}.{column} would store Python None as JSON null"
    )


async def _passage(session, selector) -> uuid.UUID:
    from athenaeum.storage.models import Paper

    paper = Paper(id=uuid.uuid4(), title="JSON null test", status="full_text")
    session.add(paper)
    await session.flush()
    passage = Passage(id=uuid.uuid4(), paper_id=paper.id, sentence_ids=[],
                      verbatim_text="some words", source_check="source_unavailable",
                      text_quote_selector=selector)
    session.add(passage)
    await session.flush()
    return passage.id


async def test_clearing_a_selector_stores_sql_null(db_session) -> None:
    """What check_passage does when a quote is no longer found. The row must
    then match IS NULL, or 'not yet anchored' queries silently miss it."""
    passage_id = await _passage(db_session, None)

    is_sql_null = (await db_session.execute(
        sa.text("SELECT text_quote_selector IS NULL FROM passages WHERE id = :i"),
        {"i": passage_id},
    )).scalar_one()

    assert is_sql_null is True


@pytest.mark.parametrize(
    "bad",
    ["null", '"a string"', "[1, 2]", '{"type": "TextQuoteSelector"}',
     '{"type": "TextPositionSelector", "exact": "x"}', '{"exact": "x"}'],
    ids=["json-null", "string", "array", "missing-exact", "wrong-type", "missing-type"],
)
async def test_selector_check_refuses_anything_but_a_real_selector(db_session, bad) -> None:
    """Written as raw SQL, so this proves the database's own guarantee rather
    than the ORM's — the CHECK is what holds if some other code path writes
    the column directly."""
    passage_id = await _passage(db_session, None)

    with pytest.raises(IntegrityError, match="passages_text_quote_selector_chk"):
        await db_session.execute(
            sa.text("UPDATE passages SET text_quote_selector = CAST(:s AS jsonb) WHERE id = :i"),
            {"s": bad, "i": passage_id},
        )


async def test_a_real_selector_is_accepted(db_session) -> None:
    await _passage(db_session, {"type": "TextQuoteSelector", "exact": "some words",
                                "prefix": "", "suffix": ""})
