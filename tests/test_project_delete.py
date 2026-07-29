"""Tests for DELETE /projects/{id} — previously didn't exist. Written
alongside migration 0022, which fixes the one gap that would have made this
endpoint fail outright: project_members.project_id had no ON DELETE CASCADE,
and every project has at least one member (its owner) by construction, so
deleting a project would hit a FK violation the moment this endpoint was
added, without that fix.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from athenaeum.api.projects import delete_project
from athenaeum.storage.models import (
    BacktraceChain,
    Paper,
    Project,
    ProjectMember,
    ProjectPaper,
    Statement,
    User,
)


async def _seed_project_with_member(session, role: str = "owner"):
    owner = User(id=uuid.uuid4(), orcid="0000-0000-0000-0004", display_name="Project Owner")
    member = User(id=uuid.uuid4(), orcid="0000-0000-0000-0005", display_name="Project Member")
    session.add_all([owner, member])
    await session.flush()

    project = Project(id=uuid.uuid4(), title="Deletable Project", owner_id=owner.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
    session.add(ProjectMember(project_id=project.id, user_id=member.id, role=role))
    await session.flush()

    return owner, member, project


async def test_owner_can_delete_project_with_members(db_session) -> None:
    """The core regression: this used to be impossible at all — the FK
    violation on project_members fired before this endpoint could even be
    written, since every project always has at least one member."""
    owner, _member, project = await _seed_project_with_member(db_session)

    await delete_project(project.id, db_session, owner)

    assert await db_session.get(Project, project.id) is None
    remaining_members = list(
        await db_session.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project.id)
        )
    )
    assert remaining_members == []


async def test_non_owner_cannot_delete_project(db_session) -> None:
    owner, member, project = await _seed_project_with_member(db_session, role="editor")

    with pytest.raises(HTTPException) as exc_info:
        await delete_project(project.id, db_session, member)
    assert exc_info.value.status_code == 403

    # Confirm it's genuinely still there, not just "the call errored."
    assert await db_session.get(Project, project.id) is not None
    del owner  # unused beyond seeding


async def test_delete_nonexistent_project_404s(db_session) -> None:
    owner, _member, _project = await _seed_project_with_member(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await delete_project(uuid.uuid4(), db_session, owner)
    assert exc_info.value.status_code == 404


async def test_delete_project_cascades_but_keeps_shared_paper(db_session) -> None:
    """Statements/chains scoped to the project disappear; a Paper (a
    cross-project, shared entity) survives — only its ProjectPaper link
    to *this* project cascades away."""
    owner, _member, project = await _seed_project_with_member(db_session)

    # Ids captured as plain values up front — after expire_all() below,
    # attribute access on the ORM objects themselves (even just `.id`)
    # would trigger a lazy-refresh, which needs async I/O and breaks with
    # a greenlet error when touched via plain synchronous attribute access.
    paper_id, statement_id, chain_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    db_session.add(Paper(id=paper_id, title="Shared Paper", status="ghost"))
    await db_session.flush()
    db_session.add(ProjectPaper(project_id=project.id, paper_id=paper_id, added_by=owner.id))

    db_session.add(Statement(
        id=statement_id, project_id=project.id, author_id=owner.id,
        type="claim", assertion="A claim that will be deleted with its project.",
    ))

    db_session.add(BacktraceChain(
        id=chain_id, project_id=project.id, created_by=owner.id,
        origin_paper_id=paper_id, origin_text="A claim to trace.",
    ))
    await db_session.flush()

    await delete_project(project.id, db_session, owner)

    # statement/chain were removed by the DB's own ON DELETE CASCADE, not
    # by an ORM-tracked delete (only the Project itself was) — Session.get()
    # would otherwise silently return the stale, still-in-identity-map
    # Python object instead of re-querying, since it checks there first.
    db_session.expire_all()

    assert await db_session.get(Statement, statement_id) is None
    assert await db_session.get(BacktraceChain, chain_id) is None
    assert await db_session.get(Paper, paper_id) is not None  # survives — shared entity
    remaining_link = await db_session.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project.id, ProjectPaper.paper_id == paper_id
        )
    )
    assert remaining_link is None
