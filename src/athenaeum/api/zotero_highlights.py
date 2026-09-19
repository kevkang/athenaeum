"""Bridges Zotero PDF annotations (highlights the user made in Zotero's own
reader) into Athenaeum's sentence-anchored Highlight model.

Two-step, same shape as Backtrace's rank-then-commit flow: preview computes
fuzzy matches against the paper's own sentence parse without writing
anything, then import persists whichever candidates the client sends back.
sentence_ids are always computed server-side and only ever round-tripped
from a prior preview call — import re-validates every id actually belongs to
the paper rather than trusting an arbitrary client-supplied span.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.crypto import decrypt_secret
from athenaeum.domain.authors import first_author_name
from athenaeum.domain.models import (
    HighlightOut,
    ZoteroHighlightCandidateOut,
    ZoteroHighlightImportRequest,
    ZoteroHighlightPreviewFromAnnotationsRequest,
    ZoteroHighlightPreviewResponse,
)
from athenaeum.ingest.highlight_match import match_annotation_to_sentences
from athenaeum.ingest.zotero import ZoteroAnnotation, ZoteroCreds, list_annotations_for_item
from athenaeum.ingest.zotero_local import (
    LocalZoteroError,
    list_local_annotations,
    resolve_configured_data_dir,
)
from athenaeum.storage.models import (
    Highlight,
    Paper,
    PaperSentence,
    ProjectMember,
    ZoteroCredentials,
)

router = APIRouter(prefix="/papers", tags=["zotero-highlights"])


def _to_out(h: Highlight, paper: Paper) -> HighlightOut:
    return HighlightOut(
        id=h.id, project_id=h.project_id, paper_id=h.paper_id,
        sentence_ids=h.sentence_ids, verbatim_text=h.verbatim_text,
        author_id=h.author_id, created_at=h.created_at,
        paper_title=paper.title, paper_year=paper.year,
        paper_first_author=first_author_name(paper.authors_json),
    )


async def _assert_member(session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")


async def _fetch_annotations(
    session: SessionDep, user_id: uuid.UUID, paper: Paper
) -> list[ZoteroAnnotation]:
    # Callers only reach here after confirming both are set (see
    # preview_zotero_highlights) — asserted here so mypy carries that
    # narrowing through, since it can't see across function boundaries.
    assert paper.zotero_item_key is not None
    if paper.zotero_source == "sync":
        row = await session.get(ZoteroCredentials, user_id)
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect your Zotero account first")
        creds = ZoteroCreds(
            zotero_user_id=row.zotero_user_id,
            api_key=decrypt_secret(row.api_key_encrypted),
            library_type=row.library_type,
        )
        try:
            return await list_annotations_for_item(creds, paper.zotero_item_key)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Could not fetch annotations from Zotero — this paper may have been imported "
                "from a different Zotero account than the one you've connected",
            ) from exc
    elif paper.zotero_source == "local":
        settings = get_settings()
        try:
            data_dir = await resolve_configured_data_dir(session, settings.zotero_local_host_mount)
            return await list_local_annotations(data_dir, paper.zotero_item_key)
        except LocalZoteroError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown zotero_source {paper.zotero_source!r}"
        )


async def _build_candidates(
    session: SessionDep,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    annotations: list[ZoteroAnnotation],
) -> ZoteroHighlightPreviewResponse:
    existing = await session.scalars(
        select(Highlight).where(Highlight.project_id == project_id, Highlight.paper_id == paper_id)
    )
    existing_spans = {frozenset(h.sentence_ids) for h in existing}

    candidates: list[ZoteroHighlightCandidateOut] = []
    for ann in annotations:
        match = await match_annotation_to_sentences(session, paper_id, ann.text)
        candidates.append(
            ZoteroHighlightCandidateOut(
                annotation_item_key=ann.item_key,
                zotero_text=ann.text,
                comment=ann.comment,
                color=ann.color,
                matched=match is not None,
                match_ratio=match.ratio if match else None,
                sentence_ids=match.sentence_ids if match else None,
                verbatim_text=match.verbatim_text if match else None,
                already_imported=bool(match and frozenset(match.sentence_ids) in existing_spans),
            )
        )
    return ZoteroHighlightPreviewResponse(candidates=candidates)


@router.get(
    "/{paper_id}/zotero-highlights/preview", response_model=ZoteroHighlightPreviewResponse
)
async def preview_zotero_highlights(
    paper_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
) -> ZoteroHighlightPreviewResponse:
    await _assert_member(session, project_id, user.id)
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    if not paper.zotero_item_key or not paper.zotero_source:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This paper wasn't imported from Zotero"
        )

    annotations = await _fetch_annotations(session, user.id, paper)
    return await _build_candidates(session, project_id, paper_id, annotations)


@router.post(
    "/{paper_id}/zotero-highlights/preview-external",
    response_model=ZoteroHighlightPreviewResponse,
)
async def preview_zotero_highlights_from_annotations(
    paper_id: uuid.UUID,
    payload: ZoteroHighlightPreviewFromAnnotationsRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> ZoteroHighlightPreviewResponse:
    """Same fuzzy-match preview as preview_zotero_highlights, but for papers
    imported via the browser extension (zotero_source == 'extension') —
    the backend has no route to the user's local Zotero for those, so the
    frontend fetches annotations through the extension itself and posts them
    here for matching. See ZOTERO_EXTENSION_PLAN.md step 7."""
    await _assert_member(session, payload.project_id, user.id)
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")

    annotations = [
        ZoteroAnnotation(
            item_key=a.item_key,
            parent_item_key="",
            annotation_type="highlight",
            text=a.text,
            comment=a.comment,
            color=a.color,
            page_label=None,
        )
        for a in payload.annotations
    ]
    return await _build_candidates(session, payload.project_id, paper_id, annotations)


@router.post(
    "/{paper_id}/zotero-highlights/import", response_model=list[HighlightOut], status_code=201
)
async def import_zotero_highlights(
    paper_id: uuid.UUID,
    payload: ZoteroHighlightImportRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[HighlightOut]:
    await _assert_member(session, payload.project_id, user.id)
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")

    valid_ids = set(
        await session.scalars(select(PaperSentence.id).where(PaperSentence.paper_id == paper_id))
    )

    created: list[Highlight] = []
    for item in payload.items:
        if not set(item.sentence_ids).issubset(valid_ids):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "sentence_ids must belong to this paper"
            )
        highlight = Highlight(
            id=uuid.uuid4(), project_id=payload.project_id, paper_id=paper_id,
            sentence_ids=item.sentence_ids, verbatim_text=item.verbatim_text,
            author_id=user.id,
        )
        session.add(highlight)
        created.append(highlight)

    await session.commit()
    for h in created:
        await session.refresh(h)
    return [_to_out(h, paper) for h in created]
