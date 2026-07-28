"""Single compose document per project — GET auto-creates, PUT is an
upsert-style autosave with optimistic-concurrency via `version`."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import ComposeDocumentOut, ComposeDocumentUpdate
from athenaeum.storage.models import ComposeDocument, ProjectMember

router = APIRouter(prefix="/projects/{project_id}/compose-document", tags=["compose"])

_EMPTY_DOC = {"type": "doc", "content": [{"type": "paragraph"}]}


async def _assert_member(session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")


async def _get_or_create(session: SessionDep, project_id: uuid.UUID) -> ComposeDocument:
    doc = await session.scalar(
        select(ComposeDocument).where(ComposeDocument.project_id == project_id)
    )
    if doc is None:
        doc = ComposeDocument(
            id=uuid.uuid4(), project_id=project_id, content_json=_EMPTY_DOC, version=1
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
    return doc


@router.get("", response_model=ComposeDocumentOut)
async def get_compose_document(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
) -> ComposeDocument:
    await _assert_member(session, project_id, user.id)
    return await _get_or_create(session, project_id)


@router.put("", response_model=ComposeDocumentOut)
async def save_compose_document(
    project_id: uuid.UUID,
    payload: ComposeDocumentUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> ComposeDocument:
    await _assert_member(session, project_id, user.id)
    doc = await _get_or_create(session, project_id)
    if doc.version != payload.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Document changed since you loaded it (server version {doc.version}, "
            f"you had {payload.version}); reload before saving again.",
        )
    doc.content_json = payload.content_json
    doc.version += 1
    doc.updated_by = user.id
    await session.commit()
    await session.refresh(doc)
    return doc
