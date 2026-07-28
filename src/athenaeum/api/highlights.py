"""Reader-pane 'maybe useful' bookmarks — personal, standalone, independent
of StatementEvidence. See annotations.py for the closest structural
precedent (also not event-logged)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.authors import first_author_name
from athenaeum.domain.models import HighlightCreate, HighlightOut
from athenaeum.storage.models import Highlight, Paper, ProjectMember

router = APIRouter(tags=["highlights"])


async def _assert_member(session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")


def _to_out(h: Highlight, paper: Paper) -> HighlightOut:
    return HighlightOut(
        id=h.id, project_id=h.project_id, paper_id=h.paper_id,
        sentence_ids=h.sentence_ids, verbatim_text=h.verbatim_text,
        author_id=h.author_id, created_at=h.created_at,
        paper_title=paper.title, paper_year=paper.year,
        paper_first_author=first_author_name(paper.authors_json),
    )


@router.post("/highlights", response_model=HighlightOut, status_code=201)
async def create_highlight(
    payload: HighlightCreate, session: SessionDep, user: CurrentUserDep,
) -> HighlightOut:
    await _assert_member(session, payload.project_id, user.id)
    paper = await session.get(Paper, payload.paper_id)
    if paper is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not found")
    highlight = Highlight(
        id=uuid.uuid4(), project_id=payload.project_id, paper_id=payload.paper_id,
        sentence_ids=payload.sentence_ids, verbatim_text=payload.verbatim_text,
        author_id=user.id,
    )
    session.add(highlight)
    await session.commit()
    await session.refresh(highlight)
    return _to_out(highlight, paper)


@router.get("/projects/{project_id}/highlights", response_model=list[HighlightOut])
async def list_highlights(
    project_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
    paper_id: uuid.UUID | None = Query(default=None),
) -> list[HighlightOut]:
    await _assert_member(session, project_id, user.id)
    stmt = select(Highlight).where(Highlight.project_id == project_id)
    if paper_id is not None:
        stmt = stmt.where(Highlight.paper_id == paper_id)
    rows = list(await session.scalars(stmt.order_by(Highlight.created_at.desc())))
    if not rows:
        return []
    papers = {
        p.id: p
        for p in await session.scalars(
            select(Paper).where(Paper.id.in_({h.paper_id for h in rows}))
        )
    }
    return [_to_out(h, papers[h.paper_id]) for h in rows if h.paper_id in papers]


@router.delete("/highlights/{highlight_id}", status_code=204)
async def delete_highlight(
    highlight_id: uuid.UUID, session: SessionDep, user: CurrentUserDep,
) -> None:
    highlight = await session.get(Highlight, highlight_id)
    if highlight is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Highlight not found")
    if highlight.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not the highlight author")
    await session.delete(highlight)
    await session.commit()
