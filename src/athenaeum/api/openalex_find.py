""""Find" over OpenAlex's global database — a lightweight, synchronous
discovery endpoint mirroring api/zotero_find.py. Unlike Zotero, OpenAlex is a
public API (no per-user credentials needed). Its own search is keyword/
relevance-based, not semantic, so a single natural-language query often
under-matches — the query is expanded into a few distinct phrasings first
(same LLM step the main synthesis pipeline uses), each searched separately,
and results merged before ranking."""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import OpenAlexFindRequest, OpenAlexFindResultOut
from athenaeum.ingest.openalex import OpenAlexSearchHit, search_works
from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user
from athenaeum.storage.models import Paper, Project, ProjectMember, ProjectPaper
from athenaeum.synthesize.find_ranking import expand_search_queries
from athenaeum.synthesize.openalex_find import rank_candidates

router = APIRouter(prefix="/openalex", tags=["openalex-find"])


async def _require_member(session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")


async def _existing_dois(session: SessionDep, project_id: uuid.UUID) -> set[str]:
    rows = (
        await session.execute(
            select(Paper.doi)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(ProjectPaper.project_id == project_id, Paper.doi.is_not(None))
        )
    ).scalars()
    return {doi for doi in rows if doi is not None}


@router.post("/find", response_model=list[OpenAlexFindResultOut])
async def find_in_openalex(
    payload: OpenAlexFindRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[OpenAlexFindResultOut]:
    await _require_member(session, payload.project_id, user.id)

    settings = get_settings()
    try:
        provider = await get_provider_for_user(
            session, user.id, settings.zotero_find_model, settings
        )
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    queries = await expand_search_queries(provider, payload.query)

    loop = asyncio.get_running_loop()
    hits_by_key: dict[str, OpenAlexSearchHit] = {}
    try:
        for q in queries:
            for hit in await loop.run_in_executor(None, search_works, q, 20):
                key = hit.openalex_id or hit.doi or hit.title
                hits_by_key.setdefault(key, hit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="OpenAlex search failed") from exc

    matches = await rank_candidates(provider, payload.query, list(hits_by_key.values()))

    existing_dois = await _existing_dois(session, payload.project_id)
    return [
        OpenAlexFindResultOut(
            openalex_id=m.hit.openalex_id,
            doi=m.hit.doi,
            title=m.hit.title,
            creators=[m.hit.first_author] if m.hit.first_author else [],
            year=m.hit.year,
            venue=m.hit.venue,
            cited_by_count=m.hit.cited_by_count,
            already_in_project=bool(m.hit.doi and m.hit.doi in existing_dois),
            quote=m.quote,
            relevance_note=m.relevance_note,
        )
        for m in matches
    ]
