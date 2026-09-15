"""Tests for checking imported quotes against this server's own copy of the
paper, end to end through import_project.

Each test builds a small .ath archive by hand with ids as the *exporting*
server would have them, then imports it into a server whose papers and
sentences have their own ids. That mismatch is the normal case the design
exists for: papers must be found by DOI, and quotes must be compared on their
words, never on ids.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from athenaeum.archive.export import build_archive
from athenaeum.archive.import_ import ATH_VERSION, import_project
from athenaeum.domain.citations import build_reference_numbering
from athenaeum.domain.source_check import (
    DIFFERS_FROM_SOURCE,
    MATCHES_SOURCE,
    SOURCE_UNAVAILABLE,
    recheck_paper,
)
from athenaeum.storage.models import (
    Highlight,
    Paper,
    PaperSection,
    PaperSentence,
    Passage,
    Statement,
    StatementEvidence,
)
from tests.test_citations import _seed_paper_with_sentence, _seed_project

TEXT = "Soil CO2 efflux rose 28% under warming."


def _doi() -> str:
    return f"10.5555/{uuid.uuid4().hex[:10]}"


def _archive(**sections) -> dict:
    return {
        "ath_version": ATH_VERSION,
        "project": {"id": str(uuid.uuid4()), "title": "Imported project"},
        **sections,
    }


def _archive_paper(doi: str) -> dict:
    """A paper as the exporting server knew it: its own id, which this server
    has never seen."""
    return {"id": str(uuid.uuid4()), "doi": doi, "title": "A warming study", "status": "full_text"}


async def _local_copy(db_session, doi: str):
    """This server's own ingested copy of a paper: its own paper id and its own
    sentence id, sharing only the DOI with the archive."""
    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Local copy", text=TEXT
    )
    paper.doi = doi
    await db_session.flush()
    return owner, paper, sentence


# ── Paper found here by DOI ──────────────────────────────────────────────────

async def test_matching_quote_is_found_by_doi_and_repointed(db_session) -> None:
    doi = _doi()
    owner, local_paper, local_sentence = await _local_copy(db_session, doi)
    ap = _archive_paper(doi)
    passage_id = uuid.uuid4()
    highlight_id = uuid.uuid4()

    result = await import_project(db_session, _archive(
        papers=[ap],
        passages=[{
            "id": str(passage_id), "paper_id": ap["id"], "sentence_ids": [str(uuid.uuid4())],
            # Same words, extracted differently: subscript and doubled space.
            "verbatim_text": "Soil CO₂ efflux rose 28%  under warming.",
        }],
        highlights=[{
            "id": str(highlight_id), "paper_id": ap["id"], "sentence_ids": [str(uuid.uuid4())],
            "verbatim_text": TEXT,
        }],
    ), owner.id)

    passage = await db_session.get(Passage, passage_id)
    assert passage is not None
    assert passage.paper_id == local_paper.id
    assert passage.source_check == MATCHES_SOURCE
    assert passage.sentence_ids == [local_sentence.id]

    highlight = await db_session.get(Highlight, highlight_id)
    assert highlight is not None
    assert highlight.sentence_ids == [local_sentence.id]

    # No duplicate paper was created under the archive's id, and nothing needs fetching.
    assert await db_session.get(Paper, uuid.UUID(ap["id"])) is None
    assert result.papers_to_fetch == []


async def test_changed_quote_is_imported_but_flagged(db_session) -> None:
    doi = _doi()
    owner, local_paper, _sentence = await _local_copy(db_session, doi)
    ap = _archive_paper(doi)
    passage_id = uuid.uuid4()
    archive_sentence_id = uuid.uuid4()

    await import_project(db_session, _archive(
        papers=[ap],
        passages=[{
            "id": str(passage_id), "paper_id": ap["id"], "sentence_ids": [str(archive_sentence_id)],
            "verbatim_text": "Soil CO2 efflux rose 82% under warming.",
        }],
    ), owner.id)

    passage = await db_session.get(Passage, passage_id)
    assert passage is not None
    assert passage.paper_id == local_paper.id
    assert passage.source_check == DIFFERS_FROM_SOURCE
    # Not re-pointed: nothing on this server contains these words.
    assert passage.sentence_ids == [archive_sentence_id]


# ── Paper not on this server ─────────────────────────────────────────────────

async def test_missing_paper_is_unavailable_queued_then_checked_on_arrival(db_session) -> None:
    owner, _project = await _seed_project(db_session)
    ap = _archive_paper(_doi())
    passage_id = uuid.uuid4()

    result = await import_project(db_session, _archive(
        papers=[ap],
        passages=[{
            "id": str(passage_id), "paper_id": ap["id"], "sentence_ids": [str(uuid.uuid4())],
            "verbatim_text": TEXT,
        }],
    ), owner.id)

    paper_id = uuid.UUID(ap["id"])
    paper = await db_session.get(Paper, paper_id)
    passage = await db_session.get(Passage, passage_id)
    assert paper is not None and passage is not None
    assert paper.status == "ghost"
    assert passage.source_check == SOURCE_UNAVAILABLE
    assert result.papers_to_fetch == [paper_id]

    # Simulate ingestion finishing: the paper's text arrives on this server.
    section = PaperSection(
        id=uuid.uuid4(), paper_id=paper_id, label="Body", ord=0, char_start=0, char_end=len(TEXT),
    )
    db_session.add(section)
    await db_session.flush()
    sentence = PaperSentence(
        id=uuid.uuid4(), paper_id=paper_id, section_id=section.id, ord=0,
        text=TEXT, char_start=0, char_end=len(TEXT),
    )
    db_session.add(sentence)
    await db_session.flush()

    counts = await recheck_paper(db_session, paper_id)

    assert counts == {MATCHES_SOURCE: 1}
    assert passage.source_check == MATCHES_SOURCE
    assert passage.sentence_ids == [sentence.id]


# ── Citations dragged from the reader (pointing straight at a sentence) ──────

async def test_export_carries_the_words_of_sentence_citations(db_session) -> None:
    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Cited paper", text=TEXT
    )
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, type="claim", status="draft", assertion="A claim.",
    )
    db_session.add(statement)
    await db_session.flush()
    db_session.add(StatementEvidence(
        statement_id=statement.id, target_kind="passage", target_id=sentence.id, role="evidence",
    ))
    await db_session.flush()

    archive = await build_archive(db_session, project.id)

    [exported] = next(s for s in archive["statements"] if s["id"] == str(statement.id))["evidence"]
    assert exported["quote"] == TEXT
    assert exported["paper_id"] == str(paper.id)


async def test_sentence_citation_with_words_imports_as_a_checked_passage(db_session) -> None:
    doi = _doi()
    owner, _local_paper, local_sentence = await _local_copy(db_session, doi)
    ap = _archive_paper(doi)
    statement_id = uuid.uuid4()

    await import_project(db_session, _archive(
        papers=[ap],
        statements=[{
            "id": str(statement_id), "type": "claim", "status": "draft", "assertion": "A claim.",
            "evidence": [{
                "target_kind": "passage", "target_id": str(uuid.uuid4()), "role": "evidence",
                "quote": TEXT, "paper_id": ap["id"],
            }],
        }],
    ), owner.id)

    # Citation numbering (what Compose and Audit use) no longer crashes...
    numbering = await build_reference_numbering(db_session, [statement_id])
    assert numbering.statement_markers[statement_id]

    # ...because the citation now points at a real, checked passage.
    [evidence] = (await db_session.scalars(
        select(StatementEvidence)
        .where(StatementEvidence.statement_id == statement_id)
    )).all()
    passage = await db_session.get(Passage, evidence.target_id)
    assert passage is not None
    assert passage.source_check == MATCHES_SOURCE
    assert passage.sentence_ids == [local_sentence.id]


async def test_old_wordless_sentence_citation_is_skipped_with_a_warning(db_session) -> None:
    """An archive from before quotes were exported gives nothing to check or
    point at. Skipping it, and saying so, beats storing a citation that crashes
    citation numbering."""
    owner, _project = await _seed_project(db_session)
    statement_id = uuid.uuid4()

    result = await import_project(db_session, _archive(
        statements=[{
            "id": str(statement_id), "type": "claim", "status": "draft", "assertion": "A claim.",
            "evidence": [{
                "target_kind": "passage", "target_id": str(uuid.uuid4()), "role": "evidence",
            }],
        }],
    ), owner.id)

    assert len(result.warnings) == 1
    assert "A claim." in result.warnings[0]
    numbering = await build_reference_numbering(db_session, [statement_id])
    assert numbering.statement_markers.get(statement_id, []) == []
