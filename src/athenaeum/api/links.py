"""Knowledge link endpoints.

POST   /links                          — retired: 410 Gone (see below)
DELETE /links/{link_id}                — remove a link
GET    /projects/{project_id}/links    — list links touching a project (optionally filtered by ref)
GET    /projects/{project_id}/refs     — unified ref search for the mention picker
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import (
    KnowledgeLinkOut,
    KnowledgeRefOut,
)
from athenaeum.storage.models import KnowledgeLink, Paper, ProjectMember, Synthesis

log = structlog.get_logger()
router = APIRouter(tags=["links"])


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _resolve_title(
    session: SessionDep, ref_type: str, ref_id: uuid.UUID
) -> str:
    if ref_type == "paper":
        paper = await session.get(Paper, ref_id)
        return paper.title if paper else f"[deleted paper {ref_id}]"
    if ref_type == "synthesis":
        synthesis = await session.get(Synthesis, ref_id)
        return synthesis.claim_text[:120] if synthesis else f"[deleted synthesis {ref_id}]"
    return str(ref_id)


def _link_to_out(link: KnowledgeLink, from_title: str, to_title: str) -> KnowledgeLinkOut:
    return KnowledgeLinkOut(
        id=link.id,
        project_id=link.project_id,
        from_type=link.from_type,
        from_id=link.from_id,
        from_title=from_title,
        to_type=link.to_type,
        to_id=link.to_id,
        to_title=to_title,
        rel=link.rel,
        note=link.note,
        created_by=link.created_by,
        created_at=link.created_at,
    )


async def _batch_resolve_titles(
    session: SessionDep, refs: set[tuple[str, uuid.UUID]]
) -> dict[tuple[str, uuid.UUID], str]:
    """Same fallback text as _resolve_title, but two queries total regardless
    of how many refs there are, instead of one `session.get` per ref."""
    paper_ids = {rid for rtype, rid in refs if rtype == "paper"}
    synthesis_ids = {rid for rtype, rid in refs if rtype == "synthesis"}

    titles: dict[tuple[str, uuid.UUID], str] = {}
    if paper_ids:
        for p in await session.scalars(select(Paper).where(Paper.id.in_(paper_ids))):
            titles[("paper", p.id)] = p.title
    if synthesis_ids:
        for s in await session.scalars(select(Synthesis).where(Synthesis.id.in_(synthesis_ids))):
            titles[("synthesis", s.id)] = s.claim_text[:120]
    return titles


async def _check_membership(
    session: SessionDep, project_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/links", status_code=status.HTTP_410_GONE)
async def create_link() -> None:
    """Retired with migration 0035. Paper → paper relations are semantic edges
    now (the six CiTO-mapped terms, per project); that migration carried the
    existing links over. Refusing new ones keeps the two tables from drifting
    apart. Nothing in the frontend ever created links. Reading and deleting
    existing rows still works."""
    raise HTTPException(
        status.HTTP_410_GONE,
        "Knowledge links are retired. Relate papers with Nucleate's semantic relations.",
    )


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    link_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    link = await session.get(KnowledgeLink, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    await _check_membership(session, link.project_id, user.id)
    await session.delete(link)
    await session.commit()


@router.get("/projects/{project_id}/links", response_model=list[KnowledgeLinkOut])
async def list_links(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    ref_type: str | None = Query(default=None),
    ref_id: uuid.UUID | None = Query(default=None),
) -> list[KnowledgeLinkOut]:
    await _check_membership(session, project_id, user.id)

    stmt = select(KnowledgeLink).where(KnowledgeLink.project_id == project_id)

    if ref_type is not None and ref_id is not None:
        stmt = stmt.where(
            or_(
                (KnowledgeLink.from_type == ref_type) & (KnowledgeLink.from_id == ref_id),
                (KnowledgeLink.to_type == ref_type) & (KnowledgeLink.to_id == ref_id),
            )
        )

    stmt = stmt.order_by(KnowledgeLink.created_at.desc())
    rows = list(await session.scalars(stmt))
    if not rows:
        return []

    refs = {(link.from_type, link.from_id) for link in rows} | {
        (link.to_type, link.to_id) for link in rows
    }
    titles = await _batch_resolve_titles(session, refs)

    result: list[KnowledgeLinkOut] = []
    for link in rows:
        from_title = titles.get(
            (link.from_type, link.from_id), f"[deleted {link.from_type} {link.from_id}]"
        )
        to_title = titles.get(
            (link.to_type, link.to_id), f"[deleted {link.to_type} {link.to_id}]"
        )
        result.append(_link_to_out(link, from_title, to_title))
    return result


@router.get("/projects/{project_id}/refs", response_model=list[KnowledgeRefOut])
async def search_refs(
    project_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[KnowledgeRefOut]:
    """Unified ref search for the mention picker — returns papers and syntheses."""
    await _check_membership(session, project_id, user.id)

    results: list[KnowledgeRefOut] = []
    pattern = f"%{q}%" if q else "%"

    # Papers in this project
    from athenaeum.storage.models import ProjectPaper
    paper_rows = list(await session.scalars(
        select(Paper)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(
            ProjectPaper.project_id == project_id,
            Paper.title.ilike(pattern),
        )
        .order_by(Paper.created_at.desc())
        .limit(limit)
    ))
    for p in paper_rows:
        parts = [str(p.year)] if p.year else []
        if p.venue:
            parts.append(p.venue)
        results.append(KnowledgeRefOut(
            id=p.id,
            ref_type="paper",
            title=p.title,
            subtitle=", ".join(parts) or None,
        ))

    # Syntheses in this project
    synth_rows = list(await session.scalars(
        select(Synthesis)
        .where(
            Synthesis.project_id == project_id,
            Synthesis.claim_text.ilike(pattern),
        )
        .order_by(Synthesis.run_at.desc())
        .limit(limit)
    ))
    for s in synth_rows:
        results.append(KnowledgeRefOut(
            id=s.id,
            ref_type="synthesis",
            title=s.claim_text[:120],
            subtitle=s.status,
        ))

    # Sort combined results: exact-prefix matches first, then by insertion order
    q_lower = q.lower()
    results.sort(key=lambda r: (0 if r.title.lower().startswith(q_lower) else 1))
    return results[:limit]
