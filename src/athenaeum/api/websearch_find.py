""""Find" via Claude's own web search — a lightweight, synchronous discovery
endpoint mirroring api/zotero_find.py and api/openalex_find.py. See
synthesize/websearch_find.py for why this corpus has no import action:
results are arbitrary web pages, not bibliographic records with a DOI."""
from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import WebSearchFindRequest, WebSearchFindResultOut
from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user
from athenaeum.storage.models import Project, ProjectMember
from athenaeum.synthesize.websearch_find import find_via_web_search

router = APIRouter(prefix="/websearch", tags=["websearch-find"])


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


@router.post("/find", response_model=list[WebSearchFindResultOut])
async def find_via_web(
    payload: WebSearchFindRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[WebSearchFindResultOut]:
    await _require_member(session, payload.project_id, user.id)

    settings = get_settings()
    try:
        provider = await get_provider_for_user(
            session, user.id, settings.zotero_find_model, settings
        )
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    try:
        matches = await find_via_web_search(provider, payload.query)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Web search failed") from exc

    return [
        WebSearchFindResultOut(
            url=m.url,
            title=m.title,
            source=urlparse(m.url).netloc or None,
            quote=m.quote,
            relevance_note=m.relevance_note,
        )
        for m in matches
    ]
