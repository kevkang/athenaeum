"""POST /search — hybrid sentence retrieval scoped to a project."""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import SearchQuery, SearchResponse
from athenaeum.ingest.embed import embed_sentences
from athenaeum.retrieve.hybrid import search
from athenaeum.storage.models import ProjectMember

log = structlog.get_logger()
router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def hybrid_search(
    payload: SearchQuery,
    session: SessionDep,
    user: CurrentUserDep,
) -> SearchResponse:
    # Enforce project membership
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == payload.project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    settings = get_settings()

    # Embed query for vector leg (skip for text-only mode)
    query_embedding: list[float] | None = None
    if payload.mode in ("vector", "hybrid"):
        query_embedding = (await embed_sentences([payload.query], settings.embed_model))[0]

    hits = await search(
        session=session,
        project_id=payload.project_id,
        query_text=payload.query,
        query_embedding=query_embedding,
        top_k=payload.top_k,
        mode=payload.mode,
    )

    log.info(
        "search.done",
        project_id=str(payload.project_id),
        mode=payload.mode,
        n_hits=len(hits),
    )
    return SearchResponse(query=payload.query, mode=payload.mode, hits=hits)
