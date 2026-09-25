"""Project export (GET /projects/{id}/export) and import (POST /projects/import)."""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
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

    yaml_str = arc_export.dumps(data)
    safe_title = data["project"]["title"][:40].replace("/", "-").replace(" ", "_")

    return StreamingResponse(
        iter([yaml_str.encode()]),
        media_type="application/x-yaml",
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
        data = arc_export.loads(raw.decode())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot parse archive: {exc}") from exc

    if not isinstance(data, dict) or data.get("ath_version") != arc_export.ATH_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported or missing ath_version (expected {arc_export.ATH_VERSION})",
        )

    try:
        result = await arc_import.import_project(session, data, user.id)
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
