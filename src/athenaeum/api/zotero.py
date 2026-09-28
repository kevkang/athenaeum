"""Per-user Zotero connection management, library search, and item import."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.crypto import EncryptionNotConfigured, decrypt_secret, encrypt_secret
from athenaeum.domain.models import (
    PaperOut,
    ZoteroBrowseResponse,
    ZoteroCollectionOut,
    ZoteroCredentialsIn,
    ZoteroCredentialsStatus,
    ZoteroImportRequest,
    ZoteroSearchResultOut,
)
from athenaeum.ingest.zotero import (
    ZoteroAuthError,
    ZoteroCreds,
    ZoteroRateLimitError,
    ZoteroSearchHit,
    browse_items,
    get_item,
    list_collections,
    search_library,
    validate_api_key,
    zotero_item_to_paper_fields,
)
from athenaeum.storage.models import Paper, Project, ProjectMember, ProjectPaper, ZoteroCredentials

log = structlog.get_logger()
router = APIRouter(prefix="/zotero", tags=["zotero"])


def _creds_from_row(row: ZoteroCredentials) -> ZoteroCreds:
    return ZoteroCreds(
        zotero_user_id=row.zotero_user_id,
        api_key=decrypt_secret(row.api_key_encrypted),
        library_type=row.library_type,
    )


async def _existing_dois(session: SessionDep, project_id: uuid.UUID) -> set[str]:
    # The SQL WHERE clause already excludes NULL dois, but Paper.doi's column
    # type is still `str | None` in general — this comprehension makes that
    # guarantee visible to the type checker too, not just to Postgres.
    rows = (
        await session.execute(
            select(Paper.doi)
            .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
            .where(ProjectPaper.project_id == project_id, Paper.doi.is_not(None))
        )
    ).scalars()
    return {doi for doi in rows if doi is not None}


def _to_result_out(hit: ZoteroSearchHit, existing_dois: set[str]) -> ZoteroSearchResultOut:
    return ZoteroSearchResultOut(
        item_key=hit.item_key,
        title=hit.title,
        creators=hit.creators,
        year=hit.year,
        venue=hit.venue,
        doi=hit.doi,
        item_type=hit.item_type,
        already_in_project=bool(hit.doi and hit.doi in existing_dois),
        date_added=hit.date_added,
    )


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


@router.get("/credentials", response_model=ZoteroCredentialsStatus)
async def get_credentials_status(
    session: SessionDep,
    user: CurrentUserDep,
) -> ZoteroCredentialsStatus:
    row = await session.get(ZoteroCredentials, user.id)
    if row is None:
        return ZoteroCredentialsStatus(connected=False)
    return ZoteroCredentialsStatus(connected=True, zotero_user_id=row.zotero_user_id)


@router.put("/credentials", response_model=ZoteroCredentialsStatus)
async def save_credentials(
    payload: ZoteroCredentialsIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> ZoteroCredentialsStatus:
    settings = get_settings()
    if not settings.secret_encryption_key:
        raise HTTPException(
            status_code=501, detail="Zotero integration not configured (set SECRET_ENCRYPTION_KEY)"
        )

    # The numeric user ID is derived from Zotero's own response, never typed
    # by the user — see validate_api_key()'s docstring for why.
    try:
        key_info = await validate_api_key(payload.api_key)
    except ZoteroAuthError as exc:
        log.warning("zotero.validate_failed", kind="auth", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid Zotero API key") from exc
    except ZoteroRateLimitError as exc:
        log.warning("zotero.validate_failed", kind="rate_limit", error=str(exc))
        raise HTTPException(
            status_code=429, detail="Zotero is rate-limiting this key — try again shortly"
        ) from exc
    except Exception as exc:
        log.warning("zotero.validate_failed", kind="unknown", error=str(exc))
        raise HTTPException(
            status_code=502, detail="Could not reach Zotero to validate credentials"
        ) from exc

    try:
        encrypted = encrypt_secret(payload.api_key)
    except EncryptionNotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    row = await session.get(ZoteroCredentials, user.id)
    if row is None:
        row = ZoteroCredentials(
            user_id=user.id,
            zotero_user_id=key_info.user_id,
            api_key_encrypted=encrypted,
        )
        session.add(row)
    else:
        row.zotero_user_id = key_info.user_id
        row.api_key_encrypted = encrypted
        row.updated_at = datetime.now(UTC)

    await session.commit()
    return ZoteroCredentialsStatus(connected=True, zotero_user_id=key_info.user_id)


@router.delete("/credentials", status_code=204)
async def delete_credentials(
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    row = await session.get(ZoteroCredentials, user.id)
    if row is not None:
        await session.delete(row)
        await session.commit()


@router.get("/search", response_model=list[ZoteroSearchResultOut])
async def search(
    session: SessionDep,
    user: CurrentUserDep,
    q: str = Query(min_length=3, max_length=500),
    project_id: uuid.UUID = Query(...),
) -> list[ZoteroSearchResultOut]:
    await _require_member(session, project_id, user.id)

    row = await session.get(ZoteroCredentials, user.id)
    if row is None:
        raise HTTPException(status_code=400, detail="Connect your Zotero account first")

    creds = _creds_from_row(row)
    try:
        hits = await search_library(creds, q)
    except ZoteroAuthError as exc:
        log.warning("zotero.search_failed", kind="auth", error=str(exc))
        raise HTTPException(
            status_code=409, detail="Your Zotero connection is no longer valid — reconnect"
        ) from exc
    except ZoteroRateLimitError as exc:
        log.warning("zotero.search_failed", kind="rate_limit", error=str(exc))
        raise HTTPException(
            status_code=429, detail="Zotero is rate-limiting this key — try again shortly"
        ) from exc
    except Exception as exc:
        log.warning("zotero.search_failed", kind="unknown", error=str(exc))
        raise HTTPException(status_code=502, detail="Zotero search failed") from exc

    existing_dois = await _existing_dois(session, project_id)
    return [_to_result_out(h, existing_dois) for h in hits]


@router.get("/collections", response_model=list[ZoteroCollectionOut])
async def list_collections_endpoint(
    session: SessionDep,
    user: CurrentUserDep,
) -> list[ZoteroCollectionOut]:
    row = await session.get(ZoteroCredentials, user.id)
    if row is None:
        raise HTTPException(status_code=400, detail="Connect your Zotero account first")
    creds = _creds_from_row(row)

    try:
        collections = await list_collections(creds)
    except ZoteroAuthError as exc:
        log.warning("zotero.collections_failed", kind="auth", error=str(exc))
        raise HTTPException(
            status_code=409, detail="Your Zotero connection is no longer valid — reconnect"
        ) from exc
    except ZoteroRateLimitError as exc:
        log.warning("zotero.collections_failed", kind="rate_limit", error=str(exc))
        raise HTTPException(
            status_code=429, detail="Zotero is rate-limiting this key — try again shortly"
        ) from exc
    except Exception as exc:
        log.warning("zotero.collections_failed", kind="unknown", error=str(exc))
        raise HTTPException(
            status_code=502, detail="Could not fetch collections from Zotero"
        ) from exc

    return [
        ZoteroCollectionOut(key=c.key, name=c.name, parent_key=c.parent_key) for c in collections
    ]


@router.get("/items", response_model=ZoteroBrowseResponse)
async def browse_items_endpoint(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
    collection_key: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=500),
    start: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default="date_added", pattern="^(title|creator|year|date_added)$"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> ZoteroBrowseResponse:
    await _require_member(session, project_id, user.id)

    row = await session.get(ZoteroCredentials, user.id)
    if row is None:
        raise HTTPException(status_code=400, detail="Connect your Zotero account first")
    creds = _creds_from_row(row)

    try:
        hits, total = await browse_items(
            creds,
            collection_key,
            start=start,
            limit=limit,
            sort=sort,
            direction=direction,
            query=q,
        )
    except ZoteroAuthError as exc:
        log.warning("zotero.browse_failed", kind="auth", error=str(exc))
        raise HTTPException(
            status_code=409, detail="Your Zotero connection is no longer valid — reconnect"
        ) from exc
    except ZoteroRateLimitError as exc:
        log.warning("zotero.browse_failed", kind="rate_limit", error=str(exc))
        raise HTTPException(
            status_code=429, detail="Zotero is rate-limiting this key — try again shortly"
        ) from exc
    except Exception as exc:
        log.warning("zotero.browse_failed", kind="unknown", error=str(exc))
        raise HTTPException(status_code=502, detail="Zotero browse failed") from exc

    existing_dois = await _existing_dois(session, project_id)
    return ZoteroBrowseResponse(
        items=[_to_result_out(h, existing_dois) for h in hits],
        total=total,
    )


async def supply_existing_paper(
    session: AsyncSession, paper_id: uuid.UUID, zotero_item_key: str, source: str,
    user_id: uuid.UUID,
) -> Paper:
    """Make a Zotero item the source of a paper that already exists here, so
    importing it fills that paper in rather than adding a second one. Used by
    both the Web API and the local import.

    A Zotero item already imported as a *different* paper is refused: the two
    papers can't be merged here, and quietly re-pointing the item would leave
    the other one without its source.
    """
    from athenaeum.api.papers import _assert_paper_visible

    await _assert_paper_visible(session, paper_id, user_id)
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found")
    other = await session.scalar(select(Paper.id).where(
        Paper.zotero_item_key == zotero_item_key, Paper.id != paper_id))
    if other is not None:
        raise HTTPException(
            status_code=409,
            detail="That Zotero item is already imported as a different paper",
        )
    if paper.zotero_item_key != zotero_item_key:
        paper.zotero_item_key = zotero_item_key
        paper.zotero_imported_by = user_id
        paper.zotero_source = source
    return paper


@router.post("/import", response_model=PaperOut, status_code=201)
async def import_item(
    payload: ZoteroImportRequest,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Paper:
    await _require_member(session, payload.project_id, user.id)

    row = await session.get(ZoteroCredentials, user.id)
    if row is None:
        raise HTTPException(status_code=400, detail="Connect your Zotero account first")
    creds = _creds_from_row(row)

    paper: Paper | None
    if payload.paper_id is not None:
        paper = await supply_existing_paper(
            session, payload.paper_id, payload.zotero_item_key, "sync", user.id)
    else:
        # Already imported this exact Zotero item (by anyone)?
        paper = await session.scalar(
            select(Paper).where(Paper.zotero_item_key == payload.zotero_item_key)
        )

    if paper is None:
        try:
            data = await get_item(creds, payload.zotero_item_key)
        except ZoteroAuthError as exc:
            log.warning("zotero.get_item_failed", kind="auth", error=str(exc))
            raise HTTPException(
                status_code=409, detail="Your Zotero connection is no longer valid — reconnect"
            ) from exc
        except ZoteroRateLimitError as exc:
            log.warning("zotero.get_item_failed", kind="rate_limit", error=str(exc))
            raise HTTPException(
                status_code=429, detail="Zotero is rate-limiting this key — try again shortly"
            ) from exc
        except Exception as exc:
            log.warning("zotero.get_item_failed", kind="unknown", error=str(exc))
            raise HTTPException(status_code=502, detail="Could not fetch item from Zotero") from exc

        fields = zotero_item_to_paper_fields(data)

        # Dedupe by DOI too, same as the DOI-ingest path in api/papers.py.
        if fields["doi"]:
            paper = await session.scalar(select(Paper).where(Paper.doi == fields["doi"]))

        if paper is None:
            paper = Paper(
                id=uuid.uuid4(),
                doi=fields["doi"],
                title=fields["title"],
                authors_json=fields["authors_json"],
                venue=fields["venue"],
                year=fields["year"],
                abstract=fields["abstract"],
                status="ghost",
                zotero_item_key=payload.zotero_item_key,
                zotero_imported_by=user.id,
                zotero_source="sync",
            )
            session.add(paper)
            await session.flush()

    already_linked = await session.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == payload.project_id,
            ProjectPaper.paper_id == paper.id,
        )
    )
    if already_linked is None:
        session.add(
            ProjectPaper(project_id=payload.project_id, paper_id=paper.id, added_by=user.id)
        )

    if payload.found_quote:
        paper.found_quote = payload.found_quote
        paper.found_relevance_note = payload.found_relevance_note
        paper.found_corpus = "zotero_sync"

    await session.commit()
    await session.refresh(paper)

    # Only (re-)enqueue ingestion for papers this Zotero import actually owns —
    # a DOI-deduped paper ingested via another path already has its own job.
    if paper.zotero_item_key == payload.zotero_item_key and paper.status != "full_text":
        try:
            await request.app.state.arq_pool.enqueue_job(
                "ingest_zotero_item", str(paper.id), paper.zotero_item_key, str(user.id)
            )
            log.info("zotero.import_enqueued", paper_id=str(paper.id))
        except Exception as exc:
            log.warning("zotero.import_enqueue_failed", paper_id=str(paper.id), error=str(exc))

    return paper
