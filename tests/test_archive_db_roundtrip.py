"""DB-backed round-trip test for the .ath archive format.

test_roundtrip.py already covers YAML-level fidelity (a hand-built dict
survives dumps()/loads()). This file covers the half PLAN.md actually calls
out as a CI gate and that was previously untested: build_archive() (DB ->
dict) and import_project() (dict -> DB) really do reproduce the original
project when run against a real database.

The test simulates "export, then import into a different Athenaeum" within
one database by deleting the source project (and its papers/passages, which
aren't project-scoped and must be deleted explicitly) after exporting, then
re-importing the exported dict and comparing the two archives.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.archive.export import build_archive, dumps, loads
from athenaeum.archive.import_ import import_project
from athenaeum.events.applier import apply_event
from athenaeum.storage.models import (
    Annotation,
    ComposeDocument,
    Dataset,
    Evaluation,
    Highlight,
    Paper,
    Passage,
    Project,
    ProjectMember,
    ProjectPaper,
    Statement,
    StatementEdge,
    StatementEvidence,
    Synthesis,
    SynthesisEvidence,
    User,
)


def _normalize(data: dict) -> dict:
    """Recursively sort list-of-dict fields by a stable key and drop the
    always-different exported_at timestamp, so comparing two archives
    doesn't depend on Postgres's unordered-by-default row order."""
    data = deepcopy(data)
    data.pop("exported_at", None)

    def key(item: dict) -> str:
        for field in ("id", "passage_id", "target_id", "to_statement_id"):
            if item.get(field):
                return str(item[field])
        return str(sorted(item.items()))

    def walk(obj):
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            items = [walk(v) for v in obj]
            if items and all(isinstance(i, dict) for i in items):
                items.sort(key=key)
            return items
        return obj

    return walk(data)


async def _seed_project(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Builds one project exercising every archive section: a paper with a
    passage, a synthesis with evidence, two statements linked by an edge,
    an annotation, an evaluation, a dataset, a provenance event, a compose
    document, and a highlight."""
    # Flushed in dependency stages rather than one big flush at the end:
    # several of these tables (ProjectPaper, SynthesisEvidence, ...) have a
    # plain FK column with no ORM relationship() back to their parent, so
    # there's nothing for SQLAlchemy's unit-of-work to infer insert order
    # from — it has to be explicit here.
    owner = User(id=uuid.uuid4(), orcid="0000-0000-0000-0001", display_name="Test Owner")
    session.add(owner)
    await session.flush()

    project_id = uuid.uuid4()
    session.add(Project(
        id=project_id, title="Roundtrip DB Project", description="seeded for testing",
        license="CC-BY-4.0", owner_id=owner.id,
    ))
    await session.flush()
    session.add(ProjectMember(project_id=project_id, user_id=owner.id, role="owner"))

    paper_id = uuid.uuid4()
    session.add(Paper(
        id=paper_id, doi="10.1234/db-roundtrip", title="A DB Round-Trip Paper",
        authors_json=[{"name": "Alice Author"}], venue="Journal of Tests", year=2025,
        abstract="We seed rows and check they survive.", status="full_text",
    ))
    await session.flush()
    session.add(ProjectPaper(project_id=project_id, paper_id=paper_id, added_by=owner.id))

    passage_id = uuid.uuid4()
    session.add(Passage(
        id=passage_id, paper_id=paper_id, sentence_ids=[uuid.uuid4()],
        verbatim_text="We seed rows and check they survive.",
        # The seeded paper has no sentences, so these words can't be checked here.
        source_check="source_unavailable",
        verified=True, verified_at=datetime.now(UTC), checker_model="claude-sonnet-5",
    ))
    await session.flush()

    synthesis_id = uuid.uuid4()
    session.add(Synthesis(
        id=synthesis_id, project_id=project_id, claim_text="Round-trips preserve data.",
        synthesis_text="The evidence supports the claim.", retrieval_corpus="project",
        model="claude-opus-4-7", checker_model="claude-sonnet-5", verification_rate=1.0,
        status="done", query_expansion_json={"synonyms": ["roundtrip"]},
    ))
    await session.flush()
    session.add(SynthesisEvidence(
        synthesis_id=synthesis_id, passage_id=passage_id, role="theory", ord=0,
    ))

    statement1_id = uuid.uuid4()
    session.add(Statement(
        id=statement1_id, project_id=project_id, author_id=owner.id, type="foundation",
        status="locked", assertion="Round-trips preserve data.",
        built_from_synthesis_id=synthesis_id, locked_at=datetime.now(UTC),
    ))
    statement2_id = uuid.uuid4()
    session.add(Statement(
        id=statement2_id, project_id=project_id, author_id=owner.id, type="synthesis",
        status="draft", assertion="This depends on the first statement.",
    ))
    await session.flush()

    session.add(StatementEvidence(
        statement_id=statement1_id, target_kind="paper", target_id=paper_id, role="theory",
    ))
    session.add(StatementEdge(
        from_statement_id=statement2_id, to_statement_id=statement1_id, relation="supports",
    ))

    session.add(Annotation(
        id=uuid.uuid4(), project_id=project_id, author_id=owner.id, target_kind="statement",
        target_id=statement1_id, body="Strong foundation statement.",
    ))
    session.add(Evaluation(
        id=uuid.uuid4(), synthesis_id=synthesis_id, passage_id=passage_id, user_id=owner.id,
        stance="agree", body="Verified empirically.",
    ))
    session.add(Dataset(
        id=uuid.uuid4(), project_id=project_id, kind="figure", title="Figure 1", storage_uri="s3://bucket/fig1.png",
    ))
    session.add(ComposeDocument(
        id=uuid.uuid4(), project_id=project_id,
        content_json={"type": "doc", "content": [{"type": "paragraph"}]},
        version=1, updated_by=owner.id,
    ))
    session.add(Highlight(
        id=uuid.uuid4(), project_id=project_id, paper_id=paper_id, sentence_ids=[uuid.uuid4()],
        verbatim_text="We seed rows and check they survive.", author_id=owner.id,
    ))
    await session.flush()
    await apply_event(
        session, project_id=project_id, actor_id=owner.id, kind="statement.locked",
        entity_kind="statement", entity_id=statement1_id,
        payload={"statement_id": str(statement1_id)},
    )

    await session.commit()
    return project_id, paper_id, owner.id


async def test_build_archive_then_import_reproduces_the_project(db_session: AsyncSession) -> None:
    project_id, paper_id, owner_id = await _seed_project(db_session)

    archive_before = await build_archive(db_session, project_id)

    # Full YAML round-trip too, not just the in-memory dict — this is what
    # actually gets written to a .ath file and handed to another Athenaeum.
    restored = loads(dumps(archive_before))

    # Simulate importing into a *different* database: delete everything the
    # source project owns. Project cascades to Synthesis/Statement/
    # Annotation/Dataset/Event/ProjectPaper; Paper (not project-scoped) must
    # be deleted separately and cascades to its own Passage/PaperSection/
    # PaperSentence rows. ProjectMember does NOT cascade from Project — its
    # FK is missing ondelete="CASCADE", unlike every sibling table (there's
    # no DELETE /projects/{id} endpoint yet, so this is currently a dormant
    # schema inconsistency rather than a live bug) — deleted explicitly here
    # so this test doesn't depend on that being fixed. Core-level DELETE,
    # not session.delete(obj): the latter tries to manage Project's loaded
    # `members` relationship itself (blanking out a composite PK column)
    # instead of letting the database cascade handle it.
    await db_session.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
    await db_session.execute(delete(Project).where(Project.id == project_id))
    await db_session.execute(delete(Paper).where(Paper.id == paper_id))
    await db_session.commit()

    assert await db_session.get(Project, project_id) is None

    result = await import_project(db_session, restored, owner_id)
    assert result.project_id == project_id

    archive_after = await build_archive(db_session, project_id)

    # The one intended difference: the archive doesn't carry the paper's text,
    # so this server has none, and the imported paper is a ghost here until
    # it's ingested, whatever its status was on the exporting server. It is
    # queued for fetching for the same reason.
    assert [p["status"] for p in archive_after["papers"]] == ["ghost"]
    for p in archive_before["papers"]:
        p["status"] = "ghost"
    assert _normalize(archive_after) == _normalize(archive_before)
    assert result.papers_to_fetch == [paper_id]


async def test_import_marks_syntheses_as_imported(db_session: AsyncSession) -> None:
    """Rates in an archive were measured on another server, so import must
    mark those syntheses; that mark is what keeps them out of this server's
    public aggregates."""
    project_id, paper_id, owner_id = await _seed_project(db_session)
    restored = loads(dumps(await build_archive(db_session, project_id)))

    await db_session.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
    await db_session.execute(delete(Project).where(Project.id == project_id))
    await db_session.execute(delete(Paper).where(Paper.id == paper_id))
    await db_session.commit()

    await import_project(db_session, restored, owner_id)

    synthesis = await db_session.scalar(select(Synthesis).where(Synthesis.project_id == project_id))
    assert synthesis is not None
    assert synthesis.imported_at is not None


async def test_import_rejects_occupied_project_id(db_session: AsyncSession) -> None:
    project_id, _paper_id, owner_id = await _seed_project(db_session)
    archive = await build_archive(db_session, project_id)

    with pytest.raises(ValueError, match="already exists"):
        await import_project(db_session, archive, owner_id)


async def test_import_is_idempotent_for_already_ingested_papers(db_session: AsyncSession) -> None:
    """Two projects can share a paper (DOI is globally unique) — importing a
    second project that references an already-ingested paper must link to
    it (ON CONFLICT DO NOTHING), not fail or duplicate it."""
    project_id, paper_id, owner_id = await _seed_project(db_session)
    archive = await build_archive(db_session, project_id)

    # Re-target the archive at a fresh project id, but keep the same paper —
    # exercises the Paper ON CONFLICT DO NOTHING path in import_project.
    archive2 = deepcopy(archive)
    new_project_id = uuid.uuid4()
    archive2["project"]["id"] = str(new_project_id)
    archive2["syntheses"] = []
    archive2["statements"] = []
    archive2["passages"] = []
    archive2["annotations"] = []
    archive2["evaluations"] = []
    archive2["datasets"] = []
    archive2["provenance"] = []
    archive2["compose_document"] = None
    archive2["highlights"] = []

    await import_project(db_session, archive2, owner_id)

    dupes = (await db_session.scalars(select(Paper).where(Paper.id == paper_id))).all()
    assert len(dupes) == 1
