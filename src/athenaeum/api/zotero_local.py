"""Local Zotero library: search/browse/import from a bind-mounted zotero.sqlite,
instead of the Web API.

No per-user credentials here — a local data directory is a single, shared,
server-wide config (see the reasoning in config.py) rather than something
each ORCID account connects individually. docker-compose.yml bind-mounts the
operator's *entire* home directory read-only (ZOTERO_LOCAL_HOST_MOUNT) once,
as infrastructure; which subfolder under that is the actual Zotero data
directory is then a runtime choice made from the "Zotero (Local)" tab's
"Select local library" flow and persisted in zotero_local_config — no browser
can hand a backend an arbitrary host filesystem path directly (there's no
native folder picker across that boundary), so the UI takes a path relative
to the mounted home directory instead."""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path, PurePosixPath

import structlog
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.api.zotero import supply_existing_paper
from athenaeum.config import get_settings
from athenaeum.domain.models import (
    PaperOut,
    ZoteroBrowseResponse,
    ZoteroBrowserImportCollectionOut,
    ZoteroBrowserImportItemOut,
    ZoteroBrowserImportParseResponse,
    ZoteroCollectionOut,
    ZoteroImportRequest,
    ZoteroLocalBrowseDirsResponse,
    ZoteroLocalDirEntry,
    ZoteroLocalSelectRequest,
    ZoteroLocalStatus,
    ZoteroSearchResultOut,
)
from athenaeum.ingest.zotero import ZoteroSearchHit, zotero_item_to_paper_fields
from athenaeum.ingest.zotero_local import (
    LocalZoteroError,
    browse_local_items,
    get_local_item,
    list_local_collections,
    parse_full_library,
    search_local_library,
)
from athenaeum.storage.models import Paper, Project, ProjectMember, ProjectPaper, ZoteroLocalConfig

log = structlog.get_logger()
router = APIRouter(prefix="/zotero-local", tags=["zotero-local"])


def _resolve_candidate(relative_path: str) -> Path:
    """Joins a user-submitted relative path against the host-home mount,
    raising if it would escape that mount (e.g. via `..` segments)."""
    settings = get_settings()
    if not settings.zotero_local_host_mount:
        raise HTTPException(
            status_code=501,
            detail="Local Zotero not available on this server (no host directory mounted)",
        )
    mount_root = Path(settings.zotero_local_host_mount).resolve()
    candidate = (mount_root / relative_path).resolve()
    if candidate != mount_root and mount_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path must be within your home directory")
    return candidate


async def _data_dir_or_404(session: SessionDep) -> str:
    config = await session.get(ZoteroLocalConfig, 1)
    if config is None:
        raise HTTPException(
            status_code=501,
            detail="No local Zotero library selected yet — use 'Select local library'",
        )
    candidate = _resolve_candidate(config.relative_path)
    if not (candidate / "zotero.sqlite").is_file():
        raise HTTPException(
            status_code=502,
            detail="The configured local Zotero path no longer contains a zotero.sqlite",
        )
    return str(candidate)


async def _existing_dois(session: SessionDep, project_id: uuid.UUID) -> set[str]:
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


@router.get("/status", response_model=ZoteroLocalStatus)
async def get_status(session: SessionDep, user: CurrentUserDep) -> ZoteroLocalStatus:
    settings = get_settings()
    if not settings.zotero_local_host_mount:
        return ZoteroLocalStatus(available=False, relative_path=None)
    config = await session.get(ZoteroLocalConfig, 1)
    if config is None:
        return ZoteroLocalStatus(available=False, relative_path=None)
    try:
        candidate = _resolve_candidate(config.relative_path)
    except HTTPException:
        return ZoteroLocalStatus(available=False, relative_path=config.relative_path)
    if not (candidate / "zotero.sqlite").is_file():
        return ZoteroLocalStatus(available=False, relative_path=config.relative_path)
    try:
        await list_local_collections(str(candidate))
        return ZoteroLocalStatus(available=True, relative_path=config.relative_path)
    except LocalZoteroError as exc:
        log.info("zotero_local.unavailable", error=str(exc))
        return ZoteroLocalStatus(available=False, relative_path=config.relative_path)


@router.post("/select", response_model=ZoteroLocalStatus)
async def select_library(
    payload: ZoteroLocalSelectRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> ZoteroLocalStatus:
    candidate = _resolve_candidate(payload.relative_path)
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="That folder doesn't exist")
    if not (candidate / "zotero.sqlite").is_file():
        raise HTTPException(status_code=400, detail="No zotero.sqlite found in that folder")

    config = await session.get(ZoteroLocalConfig, 1)
    if config is None:
        session.add(ZoteroLocalConfig(id=1, relative_path=payload.relative_path))
    else:
        config.relative_path = payload.relative_path
    await session.commit()
    return ZoteroLocalStatus(available=True, relative_path=payload.relative_path)


@router.delete("/select", status_code=204)
async def clear_library(session: SessionDep, user: CurrentUserDep) -> None:
    config = await session.get(ZoteroLocalConfig, 1)
    if config is not None:
        await session.delete(config)
        await session.commit()


@router.get("/browse-dirs", response_model=ZoteroLocalBrowseDirsResponse)
async def browse_dirs(
    user: CurrentUserDep,
    path: str = Query(default=""),
) -> ZoteroLocalBrowseDirsResponse:
    """Lists subfolders under the mounted home directory, so the "Select
    local library" UI can be a click-through folder browser instead of a
    blind path input — browsers can't hand a backend a real filesystem path
    from a native OS picker, so this is the closest equivalent."""
    candidate = _resolve_candidate(path)
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="That folder doesn't exist")

    try:
        children = list(candidate.iterdir())
    except PermissionError as exc:
        raise HTTPException(
            status_code=403, detail="Permission denied listing that folder"
        ) from exc

    entries = []
    for child in sorted(children, key=lambda p: p.name.lower()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        try:
            has_zotero_sqlite = (child / "zotero.sqlite").is_file()
        except PermissionError:
            has_zotero_sqlite = False
        entries.append(ZoteroLocalDirEntry(name=child.name, has_zotero_sqlite=has_zotero_sqlite))

    parent_path = PurePosixPath(path).parent
    parent = None if path == "" else ("" if str(parent_path) == "." else str(parent_path))

    return ZoteroLocalBrowseDirsResponse(path=path, parent=parent, entries=entries)


@router.get("/collections", response_model=list[ZoteroCollectionOut])
async def list_collections_endpoint(
    session: SessionDep, user: CurrentUserDep
) -> list[ZoteroCollectionOut]:
    data_dir = await _data_dir_or_404(session)
    try:
        collections = await list_local_collections(data_dir)
    except LocalZoteroError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        ZoteroCollectionOut(key=c.key, name=c.name, parent_key=c.parent_key) for c in collections
    ]


@router.get("/search", response_model=list[ZoteroSearchResultOut])
async def search(
    session: SessionDep,
    user: CurrentUserDep,
    q: str = Query(min_length=3, max_length=500),
    project_id: uuid.UUID = Query(...),
) -> list[ZoteroSearchResultOut]:
    await _require_member(session, project_id, user.id)
    data_dir = await _data_dir_or_404(session)
    try:
        hits = await search_local_library(data_dir, q)
    except LocalZoteroError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    existing_dois = await _existing_dois(session, project_id)
    return [_to_result_out(h, existing_dois) for h in hits]


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
    data_dir = await _data_dir_or_404(session)
    try:
        hits, total = await browse_local_items(
            data_dir,
            collection_key,
            start=start,
            limit=limit,
            sort=sort,
            direction=direction,
            query=q,
        )
    except LocalZoteroError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    existing_dois = await _existing_dois(session, project_id)
    return ZoteroBrowseResponse(items=[_to_result_out(h, existing_dois) for h in hits], total=total)


# zotero.sqlite is metadata-only; even large libraries stay well under this.
_SQLITE_MAX_BYTES = 500 * 1024 * 1024


@router.post("/browser-import/parse", response_model=ZoteroBrowserImportParseResponse)
async def parse_browser_import(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
    file: UploadFile = File(...),
) -> ZoteroBrowserImportParseResponse:
    """Parses a zotero.sqlite the browser uploaded directly from a user-picked
    folder (see web/src/lib/zoteroFolderAccess.ts) — the client-side
    counterpart to the host-mount flow above, for deployments where the
    server has no access to the operator's filesystem (i.e. anywhere but a
    single-operator local install). Nothing here is persisted: the file is
    parsed in a scratch temp dir and discarded once the response is built.
    Attachment PDFs are never uploaded at this step — only the sqlite file,
    which is metadata-only — the browser resolves attachment_relative_path
    against its own folder handle and uploads matching PDFs separately
    through the existing /papers/upload endpoint.
    """
    await _require_member(session, project_id, user.id)

    if not (file.filename or "").endswith(".sqlite"):
        raise HTTPException(status_code=400, detail="Expected a zotero.sqlite file")

    content = await file.read()
    if len(content) > _SQLITE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large to be a zotero.sqlite")

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "zotero.sqlite").write_bytes(content)
        try:
            items = await parse_full_library(tmp)
            collections = await list_local_collections(tmp)
        except LocalZoteroError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_dois = await _existing_dois(session, project_id)

    return ZoteroBrowserImportParseResponse(
        items=[
            ZoteroBrowserImportItemOut(
                item_key=it.item_key,
                title=it.title,
                creators=it.creators,
                year=it.year,
                venue=it.venue,
                doi=it.doi,
                item_type=it.item_type,
                collection_keys=it.collection_keys,
                attachment_relative_path=it.attachment_relative_path,
                already_in_project=bool(it.doi and it.doi in existing_dois),
            )
            for it in items
        ],
        collections=[
            ZoteroBrowserImportCollectionOut(key=c.key, name=c.name, parent_key=c.parent_key)
            for c in collections
        ],
    )


@router.post("/import", response_model=PaperOut, status_code=201)
async def import_item(
    payload: ZoteroImportRequest,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> Paper:
    await _require_member(session, payload.project_id, user.id)
    data_dir = await _data_dir_or_404(session)

    paper: Paper | None
    if payload.paper_id is not None:
        paper = await supply_existing_paper(
            session, payload.paper_id, payload.zotero_item_key, "local", user.id)
    else:
        paper = await session.scalar(
            select(Paper).where(Paper.zotero_item_key == payload.zotero_item_key)
        )

    if paper is None:
        try:
            data = await get_local_item(data_dir, payload.zotero_item_key)
        except LocalZoteroError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        fields = zotero_item_to_paper_fields(data)

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
                zotero_source="local",
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
        paper.found_corpus = "zotero_local"

    await session.commit()
    await session.refresh(paper)

    if paper.zotero_item_key == payload.zotero_item_key and paper.status != "full_text":
        try:
            await request.app.state.arq_pool.enqueue_job(
                "ingest_local_zotero_item", str(paper.id), paper.zotero_item_key
            )
            log.info("zotero_local.import_enqueued", paper_id=str(paper.id))
        except Exception as exc:
            log.warning(
                "zotero_local.import_enqueue_failed", paper_id=str(paper.id), error=str(exc)
            )

    return paper
