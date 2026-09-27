"""Tests for confirming which work an ambiguous citation refers to.

Confirming is the one place a person overrides the resolver, so its guards are
what stop a mis-click or a stale page from quietly changing what a claim was
checked against.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from athenaeum.api.audit import confirm_draft_citation
from athenaeum.domain.draft_audit import CONFIRMED, EXACT, NEEDS_CONFIRMATION
from athenaeum.domain.models import AuditDraftConfirm
from athenaeum.storage.models import AuditDraftCitation, AuditRun, Project, ProjectMember, User

_CANDIDATES = [
    {
        "openalex_id": "https://openalex.org/W1",
        "doi": "10.1038/one",
        "title": "Estimating taxon-specific growth rates in soil",
        "first_author": "A. Koch",
        "venue": "Nature",
        "year": 2018,
        "cited_by_count": 40,
    },
    {
        "openalex_id": "https://openalex.org/W2",
        "doi": "10.1038/two",
        "title": "Growth rates of soil bacteria revisited",
        "first_author": "B. Miller",
        "venue": "Science",
        "year": 2019,
        "cited_by_count": 7,
    },
]


async def _seed(
    session, *, resolution: str = NEEDS_CONFIRMATION
) -> tuple[User, AuditDraftCitation]:
    user = User(id=uuid.uuid4(), orcid="0000-0000-0000-0077", display_name="Confirmer")
    session.add(user)
    await session.flush()

    project = Project(id=uuid.uuid4(), title="Draft Audit API", owner_id=user.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))

    run = AuditRun(
        id=uuid.uuid4(), project_id=project.id, kind="pasted_draft",
        draft_text="Microbes grow fast (Koch et al. 2018).", status="done",
    )
    session.add(run)
    await session.flush()

    citation = AuditDraftCitation(
        id=uuid.uuid4(), audit_run_id=run.id, ord=0,
        claim_text="Microbes grow fast.", citation_text="(Koch et al. 2018)",
        resolution=resolution,
        openalex_id=None if resolution == NEEDS_CONFIRMATION else "https://openalex.org/W9",
        candidates_json=_CANDIDATES if resolution == NEEDS_CONFIRMATION else None,
    )
    session.add(citation)
    await session.flush()
    return user, citation


async def test_confirming_records_the_chosen_work(db_session) -> None:
    user, citation = await _seed(db_session)

    result = await confirm_draft_citation(
        citation.id,
        AuditDraftConfirm(openalex_id="https://openalex.org/W2"),
        db_session,
        user,
    )

    assert result.resolution == CONFIRMED
    assert result.openalex_id == "https://openalex.org/W2"
    assert result.doi == "10.1038/two"
    assert result.matched_title == "Growth rates of soil bacteria revisited"
    assert result.matched_year == 2019


async def test_confirming_cannot_supply_a_work_that_was_never_offered(db_session) -> None:
    """Confirming is choosing between the candidates found for this citation,
    not naming an arbitrary paper for the claim to be judged against."""
    user, citation = await _seed(db_session)

    with pytest.raises(HTTPException) as exc:
        await confirm_draft_citation(
            citation.id,
            AuditDraftConfirm(openalex_id="https://openalex.org/W999"),
            db_session,
            user,
        )

    assert exc.value.status_code == 400
    assert "candidates" in str(exc.value.detail)


async def test_an_already_resolved_citation_cannot_be_reconfirmed(db_session) -> None:
    """A stale page must not overwrite a match that was settled deterministically."""
    user, citation = await _seed(db_session, resolution=EXACT)

    with pytest.raises(HTTPException) as exc:
        await confirm_draft_citation(
            citation.id,
            AuditDraftConfirm(openalex_id="https://openalex.org/W1"),
            db_session,
            user,
        )

    assert exc.value.status_code == 409


async def test_a_non_member_cannot_confirm(db_session) -> None:
    _, citation = await _seed(db_session)
    outsider = User(id=uuid.uuid4(), orcid="0000-0000-0000-0078", display_name="Outsider")
    db_session.add(outsider)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await confirm_draft_citation(
            citation.id,
            AuditDraftConfirm(openalex_id="https://openalex.org/W1"),
            db_session,
            outsider,
        )

    assert exc.value.status_code == 403


async def test_a_missing_citation_is_a_404(db_session) -> None:
    user, _ = await _seed(db_session)

    with pytest.raises(HTTPException) as exc:
        await confirm_draft_citation(
            uuid.uuid4(), AuditDraftConfirm(openalex_id="x"), db_session, user
        )

    assert exc.value.status_code == 404
