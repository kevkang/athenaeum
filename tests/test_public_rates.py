"""Tests for the public Synthesis rate endpoints: what they count, and what
they must leave out.

These numbers are published without authentication, so the tests pin down
scope rather than arithmetic: private projects, imported syntheses, and
unfinished syntheses must never move a public rate, and a scope with nothing
measured reports no rate rather than 0. The private-project test guards a
real bug: the old query cross-joined the whole syntheses table into its
average, so private projects silently counted toward the public number.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.public import (
    get_public_project,
    get_synthesis_relevance_rate,
    get_verification_rate,
)
from athenaeum.storage.models import Project, ProjectMember, Synthesis, User

# Each seeded owner needs a unique ORCID; the 8888 block avoids other modules' values.
_orcid_counter = itertools.count(1)


async def _project(session: AsyncSession, *, is_public: bool) -> Project:
    owner = User(
        id=uuid.uuid4(),
        orcid=f"0000-0000-8888-{next(_orcid_counter):04d}",
        display_name="Rates Tester",
    )
    session.add(owner)
    await session.flush()
    project = Project(
        id=uuid.uuid4(), title="Rates Test Project", owner_id=owner.id, is_public=is_public,
    )
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
    return project


async def _synthesis(
    session: AsyncSession,
    project: Project,
    *,
    relevance_rate: float | None = None,
    verification_rate: float | None = None,
    status: str = "done",
    imported: bool = False,
) -> None:
    session.add(Synthesis(
        id=uuid.uuid4(),
        project_id=project.id,
        claim_text="Warming increases soil CO2 efflux.",
        retrieval_corpus="project",
        model="test-model",
        status=status,
        relevance_rate=relevance_rate,
        verification_rate=verification_rate,
        imported_at=datetime.now(UTC) if imported else None,
    ))
    await session.flush()


async def test_relevance_rate_counts_only_finished_local_syntheses(db_session) -> None:
    project = await _project(db_session, is_public=True)
    await _synthesis(db_session, project, relevance_rate=0.8)
    await _synthesis(db_session, project, relevance_rate=0.6)
    await _synthesis(db_session, project, relevance_rate=1.0, imported=True)
    await _synthesis(db_session, project, relevance_rate=0.1, status="running")
    await _synthesis(db_session, project, relevance_rate=None)  # done, nothing measured

    out = await get_synthesis_relevance_rate(db_session, project_id=project.id)

    assert out.synthesis_count == 2
    assert out.avg_relevance_rate == pytest.approx(0.7)


async def test_private_syntheses_never_move_the_public_rate(db_session) -> None:
    """Regression test for the cross-join bug. Measured across all public
    projects, so it compares before and after rather than asserting an
    absolute value that other rows in the database could change."""
    public = await _project(db_session, is_public=True)
    await _synthesis(db_session, public, relevance_rate=0.5, verification_rate=0.5)
    before_rr = await get_synthesis_relevance_rate(db_session, project_id=None)
    before_vr = await get_verification_rate(db_session, project_id=None)

    private = await _project(db_session, is_public=False)
    for _ in range(5):
        await _synthesis(db_session, private, relevance_rate=0.0, verification_rate=0.0)

    after_rr = await get_synthesis_relevance_rate(db_session, project_id=None)
    after_vr = await get_verification_rate(db_session, project_id=None)

    assert after_rr == before_rr
    assert after_vr == before_vr


async def test_scope_with_nothing_measured_reports_no_rate(db_session) -> None:
    """An unmeasured rate and a measured rate of zero are different claims."""
    project = await _project(db_session, is_public=True)
    await _synthesis(db_session, project, relevance_rate=None)

    out = await get_synthesis_relevance_rate(db_session, project_id=project.id)

    assert out.synthesis_count == 0
    assert out.avg_relevance_rate is None


async def test_retired_rate_is_scoped_the_same_way(db_session) -> None:
    project = await _project(db_session, is_public=True)
    await _synthesis(db_session, project, verification_rate=0.5)
    await _synthesis(db_session, project, verification_rate=1.0, imported=True)

    out = await get_verification_rate(db_session, project_id=project.id)

    assert out.synthesis_count == 1
    assert out.avg_verification_rate == pytest.approx(0.5)


async def test_private_project_is_not_found(db_session) -> None:
    project = await _project(db_session, is_public=False)

    with pytest.raises(HTTPException) as exc:
        await get_synthesis_relevance_rate(db_session, project_id=project.id)

    assert exc.value.status_code == 404


async def test_public_project_stats_exclude_imported_syntheses(db_session) -> None:
    project = await _project(db_session, is_public=True)
    await _synthesis(db_session, project, relevance_rate=0.4)
    await _synthesis(db_session, project, relevance_rate=1.0, imported=True)

    out = await get_public_project(project.id, db_session)

    assert out.avg_relevance_rate == pytest.approx(0.4)
