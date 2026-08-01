"""Single compose document per project — GET auto-creates, PUT is an
upsert-style autosave with optimistic-concurrency via `version`."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.archive.athmd import build_athmd, import_athmd
from athenaeum.domain.authors import first_author_name
from athenaeum.domain.citations import build_reference_numbering, iter_statement_ids_in_order
from athenaeum.domain.models import (
    CitationEvidenceOut,
    CitationNumberingOut,
    CitationReferenceOut,
    ComposeDocumentOut,
    ComposeDocumentUpdate,
    ComposeImportResultOut,
    ComposeImportWarningOut,
)
from athenaeum.storage.models import ComposeDocument, Project, ProjectMember

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


@router.get("/citations", response_model=CitationNumberingOut)
async def get_compose_document_citations(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
) -> CitationNumberingOut:
    """The single shared numbering pass — see domain/citations.py. Computed
    fresh from the currently persisted document on every call; nothing here
    is cached or persisted itself."""
    await _assert_member(session, project_id, user.id)
    doc = await _get_or_create(session, project_id)

    statement_ids = iter_statement_ids_in_order(doc.content_json)
    numbering = await build_reference_numbering(session, statement_ids)

    references = [
        CitationReferenceOut(
            number=ref.number,
            kind=ref.key.kind,
            id=ref.key.id,
            paper_title=ref.paper.title if ref.paper else None,
            paper_year=ref.paper.year if ref.paper else None,
            paper_first_author=first_author_name(ref.paper.authors_json) if ref.paper else None,
            paper_doi=ref.paper.doi if ref.paper else None,
            dataset_title=ref.dataset.title if ref.dataset else None,
            dataset_kind=ref.dataset.kind if ref.dataset else None,
            statement_assertion=ref.statement.assertion if ref.statement else None,
            evidence=[
                CitationEvidenceOut(
                    sub_id=e.sub_id, target_kind=e.target_kind, target_id=e.target_id,
                    quote=e.quote, role=e.role,
                )
                for e in ref.evidence
            ],
        )
        for ref in numbering.references
    ]
    return CitationNumberingOut(
        references=references, statement_markers=numbering.statement_markers
    )


@router.get("/export")
async def export_compose_document(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
) -> StreamingResponse:
    """Download the compose document as a `.athmd` file — see
    archive/athmd.py's module docstring."""
    await _assert_member(session, project_id, user.id)
    doc = await _get_or_create(session, project_id)
    project = await session.get(Project, project_id)

    text = await build_athmd(session, doc.id)
    safe_title = (project.title if project else "document")[:60].replace("/", "-").replace(" ", "_")

    return StreamingResponse(
        iter([text.encode()]),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.athmd"'},
    )


@router.post("/import", response_model=ComposeImportResultOut)
async def import_compose_document(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUserDep, file: UploadFile,
) -> ComposeImportResultOut:
    """Replace the compose document's prose from an uploaded `.athmd` file —
    see archive/athmd.py's module docstring for what does and doesn't
    survive the round trip. Reuses the same persistence mechanics as
    `save_compose_document` (fetch-or-create, bump version, commit) rather
    than a second path; there's no meaningful "client's last-known version"
    for a freshly uploaded external file, so this always overwrites the
    current document rather than checking it."""
    await _assert_member(session, project_id, user.id)

    raw = await file.read()
    try:
        text = raw.decode()
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not valid text: {exc}") from exc

    try:
        result = await import_athmd(session, text, project_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Cannot parse .athmd: {exc}") from exc

    doc = await _get_or_create(session, project_id)
    doc.content_json = result["content_json"]
    doc.version += 1
    doc.updated_by = user.id
    await session.commit()
    await session.refresh(doc)

    return ComposeImportResultOut(
        document=ComposeDocumentOut.model_validate(doc),
        warnings=[ComposeImportWarningOut(**w) for w in result["warnings"]],
    )
