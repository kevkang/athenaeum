"""Project export (GET /projects/{id}/export) and import (POST /projects/import),
and the published description of the format: its JSON Schema and JSON-LD
context, under /formats/ath/2.0/.
"""
from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.archive import ath_v2
from athenaeum.archive import export as arc_export
from athenaeum.archive import import_ as arc_import
from athenaeum.storage.models import ProjectMember

log = structlog.get_logger()
router = APIRouter(tags=["archive"])


@router.get("/projects/{project_id}/export")
async def export_project(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> StreamingResponse:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")

    try:
        data = await arc_export.build_archive(session, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    body = arc_export.dumps_json(data)
    safe_title = data["project"]["title"][:40].replace("/", "-").replace(" ", "_")

    return StreamingResponse(
        iter([body.encode()]),
        media_type=ath_v2.MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.ath"'},
    )


@router.post("/projects/import", status_code=201)
async def import_project(
    file: UploadFile,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
) -> dict:
    raw = await file.read()
    try:
        text = raw.decode()
        # Version 2 is JSON; a legacy version 1 archive is YAML.
        data = json.loads(text) if text.lstrip().startswith("{") else arc_export.loads(text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot parse archive: {exc}") from exc

    if arc_import.archive_version(data) is None:
        raise HTTPException(
            status_code=400,
            detail="Not a .ath archive this server can read (versions 1 and 2.0)",
        )

    try:
        result = await arc_import.import_project(session, data, user.id)
    except ath_v2.InvalidArchive as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        # Import keeps the archive's ids, so records already on this server
        # (the same archive imported before, under another project id) clash.
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Records in this archive already exist on this server; "
                   "it looks like it was imported here before.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Try to fetch papers this server has no text for. Their quotes were
    # imported as source_unavailable and get re-checked when ingestion
    # finishes. Best effort: the import itself has already been committed.
    papers_fetching = 0
    arq_pool = getattr(request.app.state, "arq_pool", None)
    if arq_pool is None and result.papers_to_fetch:
        log.warning("import.fetch_skipped_no_queue", n_papers=len(result.papers_to_fetch))
    elif arq_pool is not None:
        for paper_id in result.papers_to_fetch:
            try:
                await arq_pool.enqueue_job("ingest_paper", str(paper_id), oa_pdf_url=None)
                papers_fetching += 1
            except Exception:
                log.warning("import.fetch_enqueue_failed", paper_id=str(paper_id))

    return {
        "project_id": str(result.project_id),
        "papers_fetching": papers_fetching,
        "warnings": result.warnings,
    }


# ── The format, published ────────────────────────────────────────────────────


@router.get("/formats/ath/2.0/schema.json")
async def ath_schema() -> JSONResponse:
    """The JSON Schema every version 2.0 archive validates against."""
    return JSONResponse(ath_v2.schema(), media_type="application/schema+json")


@router.get("/formats/ath/2.0/context.jsonld")
async def ath_context() -> JSONResponse:
    """The JSON-LD context each archive also carries inline."""
    return JSONResponse({"@context": ath_v2.context()}, media_type="application/ld+json")
