"""Tests for the pasted-draft audit vocabulary and the shapes the database
refuses to store.

The vocabulary tests are the same drift guard used for audit verdicts and
relations: a name lives in Python and again in a CHECK constraint, and these
fail if the two ever disagree. The constraint tests pin the distinctions the
feature exists to preserve — an unresolved citation is not a failed one, and a
finding belongs to exactly one kind of thing.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from athenaeum.domain.draft_audit import (
    ABSTRACT,
    AUDIT_RUN_KIND_DEFINITIONS,
    AUDIT_RUN_KINDS,
    AUDITABLE_RESOLUTIONS,
    CONFIRMED,
    EVIDENCE_SOURCE_DEFINITIONS,
    EVIDENCE_SOURCES,
    EXACT,
    NEEDS_CONFIRMATION,
    NO_TEXT,
    NOT_FOUND,
    PROJECT_FULL_TEXT,
    RESOLUTION_DEFINITIONS,
    RESOLUTIONS,
    SOURCES_WITH_TEXT,
)
from athenaeum.storage.models import (
    AuditDraftCitation,
    AuditFinding,
    AuditRun,
    ComposeDocument,
    Project,
    User,
)

# ── Vocabulary ───────────────────────────────────────────────────────────────

def test_every_value_has_a_definition() -> None:
    assert set(RESOLUTION_DEFINITIONS) == set(RESOLUTIONS)
    assert set(EVIDENCE_SOURCE_DEFINITIONS) == set(EVIDENCE_SOURCES)
    assert set(AUDIT_RUN_KIND_DEFINITIONS) == set(AUDIT_RUN_KINDS)


def test_only_deterministic_and_confirmed_matches_get_audited() -> None:
    """The whole safety argument: a model's ranking never reaches an audit on
    its own, because a wrong match yields a confident verdict about a paper the
    author never cited."""
    assert set(AUDITABLE_RESOLUTIONS) == {EXACT, CONFIRMED}
    assert NEEDS_CONFIRMATION not in AUDITABLE_RESOLUTIONS
    assert NOT_FOUND not in AUDITABLE_RESOLUTIONS


def test_exact_is_defined_as_deterministic_not_as_a_judgement() -> None:
    text = RESOLUTION_DEFINITIONS[EXACT]

    assert "deterministic" in text
    assert "No model judgement" in text


def test_not_found_definition_separates_unchecked_from_failed() -> None:
    """If this distinction is ever lost in the wording, the UI will lose it too."""
    assert "not the same as" in RESOLUTION_DEFINITIONS[NOT_FOUND]
    assert "Not a failed check" in EVIDENCE_SOURCE_DEFINITIONS[NO_TEXT]


def test_abstract_definition_carries_the_overreach_caveat() -> None:
    assert "overreach" in EVIDENCE_SOURCE_DEFINITIONS[ABSTRACT]


def test_no_text_is_not_a_source_with_text() -> None:
    assert set(SOURCES_WITH_TEXT) == {PROJECT_FULL_TEXT, ABSTRACT}


@pytest.mark.parametrize(
    ("model", "constraint", "expected"),
    [
        (AuditRun, "audit_runs_kind_chk", set(AUDIT_RUN_KINDS)),
        (AuditDraftCitation, "audit_draft_citations_resolution_chk", set(RESOLUTIONS)),
        (
            AuditDraftCitation,
            "audit_draft_citations_evidence_source_chk",
            set(EVIDENCE_SOURCES),
        ),
    ],
)
def test_database_constraints_allow_exactly_the_vocabulary(model, constraint, expected) -> None:
    c = next(c for c in model.__table__.constraints if c.name == constraint)

    allowed = set(re.findall(r"'([a-z_]+)'", str(c.sqltext)))

    assert allowed == expected


# ── What the database refuses to store ───────────────────────────────────────

async def _project(session) -> tuple[User, Project]:
    user = User(id=uuid.uuid4(), orcid="0000-0000-0000-0099", display_name="Draft Tester")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Draft Audit", owner_id=user.id)
    session.add(project)
    await session.flush()
    return user, project


def _draft_run(project_id: uuid.UUID) -> AuditRun:
    return AuditRun(
        id=uuid.uuid4(), project_id=project_id, kind="pasted_draft",
        draft_text="Microbes grow fast (Koch et al. 2018).", status="running",
    )


async def test_a_draft_run_stores_its_text_and_no_document(db_session) -> None:
    _, project = await _project(db_session)
    run = _draft_run(project.id)
    db_session.add(run)
    await db_session.flush()

    assert run.compose_document_id is None
    assert run.draft_text is not None


async def test_a_draft_run_may_not_also_claim_a_compose_document(db_session) -> None:
    _, project = await _project(db_session)
    doc = ComposeDocument(id=uuid.uuid4(), project_id=project.id, content_json={}, version=1)
    db_session.add(doc)
    await db_session.flush()

    run = _draft_run(project.id)
    run.compose_document_id = doc.id
    db_session.add(run)

    with pytest.raises(IntegrityError, match="audit_runs_kind_payload_chk"):
        await db_session.flush()


async def test_a_compose_run_may_not_be_missing_its_document(db_session) -> None:
    _, project = await _project(db_session)
    db_session.add(
        AuditRun(
            id=uuid.uuid4(), project_id=project.id, kind="compose_document", status="running"
        )
    )

    with pytest.raises(IntegrityError, match="audit_runs_kind_payload_chk"):
        await db_session.flush()


async def test_a_finding_may_not_anchor_to_both_a_statement_and_a_citation(db_session) -> None:
    _, project = await _project(db_session)
    run = _draft_run(project.id)
    db_session.add(run)
    await db_session.flush()

    citation = AuditDraftCitation(
        id=uuid.uuid4(), audit_run_id=run.id, ord=0,
        claim_text="Microbes grow fast.", citation_text="(Koch et al. 2018)",
        resolution=EXACT, openalex_id="W1", evidence_source=ABSTRACT,
    )
    db_session.add(citation)
    await db_session.flush()

    db_session.add(
        AuditFinding(
            id=uuid.uuid4(), audit_run_id=run.id, ord=0,
            statement_id=uuid.uuid4(), draft_citation_id=citation.id,
            sub_id="1.1", supported=True, rationale="x",
        )
    )

    with pytest.raises(IntegrityError, match="audit_findings_anchor_chk"):
        await db_session.flush()


async def test_a_finding_must_anchor_to_something(db_session) -> None:
    _, project = await _project(db_session)
    run = _draft_run(project.id)
    db_session.add(run)
    await db_session.flush()

    db_session.add(
        AuditFinding(
            id=uuid.uuid4(), audit_run_id=run.id, ord=0,
            sub_id="1.1", supported=True, rationale="x",
        )
    )

    with pytest.raises(IntegrityError, match="audit_findings_anchor_chk"):
        await db_session.flush()


async def test_an_unresolved_citation_carries_no_matched_work(db_session) -> None:
    """not_found means nothing was checked, so there must be nothing to
    mistake for something that was."""
    _, project = await _project(db_session)
    run = _draft_run(project.id)
    db_session.add(run)
    await db_session.flush()

    db_session.add(
        AuditDraftCitation(
            id=uuid.uuid4(), audit_run_id=run.id, ord=0,
            claim_text="Microbes grow fast.", citation_text="(Koch et al. 2018)",
            resolution=NOT_FOUND, openalex_id="W1",
        )
    )

    with pytest.raises(IntegrityError, match="audit_draft_citations_not_found_chk"):
        await db_session.flush()
