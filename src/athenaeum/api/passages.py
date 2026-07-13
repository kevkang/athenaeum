"""Read a single passage with enough of its source paper's metadata to render
an evidence citation (e.g. "Borton et al. 2026") — needed by the workspace's
evidence-preview popover, which otherwise only has a bare passage UUID.

Evidence tagged target_kind='passage' doesn't always point at a real Passage
row: the reader pane's drag-to-workspace flow (WorkspaceView.tsx) links a
statement straight to a PaperSentence id, since a synthesis-grade Passage
(with verified/checker_model bookkeeping) is more machinery than a single
dragged sentence needs. This endpoint resolves either."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import PassageEvidenceOut
from athenaeum.storage.models import Paper, PaperSentence, Passage

router = APIRouter(prefix="/passages", tags=["passages"])


def _first_author(authors_json: Any) -> str | None:
    if isinstance(authors_json, list) and authors_json:
        first = authors_json[0]
        if isinstance(first, dict):
            return first.get("name") or first.get("display_name")
        if isinstance(first, str):
            return first
    if isinstance(authors_json, dict):
        names = authors_json.get("names")
        if isinstance(names, list) and names:
            return names[0]
    return None


@router.get("/{passage_id}", response_model=PassageEvidenceOut)
async def get_passage(
    passage_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> PassageEvidenceOut:
    passage = await session.get(Passage, passage_id)
    if passage is not None:
        paper_id = passage.paper_id
        verbatim_text = passage.verbatim_text
        verified = passage.verified
    else:
        sentence = await session.get(PaperSentence, passage_id)
        if sentence is None:
            raise HTTPException(status_code=404, detail="Passage not found")
        paper_id = sentence.paper_id
        verbatim_text = sentence.text
        verified = False

    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Source paper not found")

    return PassageEvidenceOut(
        id=passage_id,
        paper_id=paper_id,
        verbatim_text=verbatim_text,
        verified=verified,
        paper_title=paper.title,
        paper_year=paper.year,
        paper_first_author=_first_author(paper.authors_json),
    )
