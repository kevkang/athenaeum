""""Find" over a user's whole Zotero library (sync or local) — a lightweight,
synchronous discovery endpoint, distinct from the main synthesis pipeline.
See synthesize/zotero_find.py for why this doesn't reuse the Synthesis
schema."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.api.zotero_local import _data_dir_or_404
from athenaeum.config import get_settings
from athenaeum.crypto import decrypt_secret
from athenaeum.domain.models import ZoteroFindRequest, ZoteroFindResultOut
from athenaeum.ingest.zotero import (
    ZoteroAuthError,
    ZoteroCreds,
    ZoteroRateLimitError,
    ZoteroSearchHit,
    search_library_everything,
)
from athenaeum.ingest.zotero_local import LocalZoteroError, search_local_library_broad
from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user
from athenaeum.storage.models import Paper, Project, ProjectMember, ProjectPaper, ZoteroCredentials
from athenaeum.synthesize.zotero_find import extract_search_terms, rank_candidates

router = APIRouter(prefix="/zotero", tags=["zotero-find"])


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


async def _search_hits(
    session: SessionDep, user_id: uuid.UUID, corpus: str, terms: list[str]
) -> list[ZoteroSearchHit]:
    hits_by_key: dict[str, ZoteroSearchHit] = {}

    if corpus == "zotero_sync":
        creds_row = await session.get(ZoteroCredentials, user_id)
        if creds_row is None:
            raise HTTPException(status_code=400, detail="Connect your Zotero account first")
        creds = ZoteroCreds(
            zotero_user_id=creds_row.zotero_user_id,
            api_key=decrypt_secret(creds_row.api_key_encrypted),
            library_type=creds_row.library_type,
        )
        try:
            for term in terms:
                for hit in await search_library_everything(creds, term):
                    hits_by_key.setdefault(hit.item_key, hit)
        except ZoteroAuthError as exc:
            raise HTTPException(
                status_code=409, detail="Your Zotero connection needs to be reconnected"
            ) from exc
        except ZoteroRateLimitError as exc:
            raise HTTPException(
                status_code=429, detail="Zotero is rate-limiting this key — try again shortly"
            ) from exc
    else:
        data_dir = await _data_dir_or_404(session)
        try:
            for term in terms:
                for hit in await search_local_library_broad(data_dir, term):
                    hits_by_key.setdefault(hit.item_key, hit)
        except LocalZoteroError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return list(hits_by_key.values())


@router.post("/find", response_model=list[ZoteroFindResultOut])
async def find_in_zotero(
    payload: ZoteroFindRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[ZoteroFindResultOut]:
    await _require_member(session, payload.project_id, user.id)

    settings = get_settings()
    try:
        provider = await get_provider_for_user(
            session, user.id, settings.zotero_find_model, settings
        )
    except NoLLMKeyConfigured as exc:
        raise HTTPException(status_code=503, detail="LLM provider not configured") from exc

    terms = extract_search_terms(payload.query)
    if not terms:
        return []

    hits = await _search_hits(session, user.id, payload.corpus, terms)
    matches = await rank_candidates(provider, payload.query, hits)

    existing_dois = await _existing_dois(session, payload.project_id)
    return [
        ZoteroFindResultOut(
            item_key=m.hit.item_key,
            title=m.hit.title,
            creators=m.hit.creators,
            year=m.hit.year,
            venue=m.hit.venue,
            doi=m.hit.doi,
            item_type=m.hit.item_type,
            already_in_project=bool(m.hit.doi and m.hit.doi in existing_dois),
            quote=m.quote,
            relevance_note=m.relevance_note,
        )
        for m in matches
    ]
