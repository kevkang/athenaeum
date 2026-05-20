"""Project membership management: list, invite, update role, remove."""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import MemberAdd, MemberOut, MemberRoleUpdate
from athenaeum.storage.models import Project, ProjectMember, User

log = structlog.get_logger()
router = APIRouter(prefix="/projects", tags=["members"])


async def _require_owner(session, project_id: uuid.UUID, user_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Only the project owner can manage members")
    return project


async def _require_member(session, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    m = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if m is None:
        raise HTTPException(status_code=403, detail="Not a project member")


@router.get("/{project_id}/members", response_model=list[MemberOut])
async def list_members(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[ProjectMember]:
    await _require_member(session, project_id, user.id)
    rows = await session.scalars(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    return list(rows)


@router.post("/{project_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    project_id: uuid.UUID,
    payload: MemberAdd,
    session: SessionDep,
    user: CurrentUserDep,
) -> ProjectMember:
    await _require_owner(session, project_id, user.id)

    target = await session.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    existing = await session.get(ProjectMember, (project_id, payload.user_id))
    if existing is not None:
        return existing

    member = ProjectMember(
        project_id=project_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


@router.patch("/{project_id}/members/{target_user_id}", response_model=MemberOut)
async def update_member_role(
    project_id: uuid.UUID,
    target_user_id: uuid.UUID,
    payload: MemberRoleUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> ProjectMember:
    await _require_owner(session, project_id, user.id)

    member = await session.get(ProjectMember, (project_id, target_user_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == "owner":
        raise HTTPException(status_code=409, detail="Cannot change the owner's role")

    member.role = payload.role
    await session.commit()
    await session.refresh(member)
    return member


@router.delete("/{project_id}/members/{target_user_id}", status_code=204)
async def remove_member(
    project_id: uuid.UUID,
    target_user_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    await _require_owner(session, project_id, user.id)

    member = await session.get(ProjectMember, (project_id, target_user_id))
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.role == "owner":
        raise HTTPException(status_code=409, detail="Cannot remove the project owner")

    await session.delete(member)
    await session.commit()
