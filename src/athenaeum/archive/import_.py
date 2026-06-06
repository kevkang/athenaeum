"""Reconstruct a project from a .ath archive dict."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.storage.models import (
    Annotation,
    Brick,
    BrickBuildsOn,
    BrickRestsOn,
    Dataset,
    Evaluation,
    Paper,
    Passage,
    Project,
    ProjectMember,
    ProjectPaper,
    Event,
    ProjectEventSeq,
    Synthesis,
    SynthesisEvidence,
)

ATH_VERSION = 1


def _u(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


async def import_project(
    session: AsyncSession,
    data: dict[str, Any],
    owner_id: uuid.UUID,
) -> uuid.UUID:
    if data.get("ath_version") != ATH_VERSION:
        raise ValueError(f"Unsupported .ath version: {data.get('ath_version')!r}")

    proj = data["project"]
    project_id = uuid.UUID(proj["id"])

    # Fail fast if project UUID already occupied.
    existing = await session.get(Project, project_id)
    if existing is not None:
        raise ValueError(f"Project {project_id} already exists in this database")

    session.add(Project(
        id=project_id,
        title=proj["title"],
        description=proj.get("description"),
        license=proj.get("license", "CC-BY-4.0"),
        owner_id=owner_id,
    ))
    session.add(ProjectMember(project_id=project_id, user_id=owner_id, role="owner"))
    await session.flush()

    # Papers — DOI and openalex_id are globally unique, so use ON CONFLICT DO NOTHING.
    for p in data.get("papers", []):
        await session.execute(
            pg_insert(Paper)
            .values(
                id=uuid.UUID(p["id"]),
                doi=p.get("doi"),
                openalex_id=p.get("openalex_id"),
                title=p["title"],
                authors_json=p.get("authors_json") or [],
                venue=p.get("venue"),
                year=p.get("year"),
                abstract=p.get("abstract"),
                status=p.get("status", "ghost"),
            )
            .on_conflict_do_nothing()
        )
        # Link paper (existing or new) to the project.
        await session.execute(
            pg_insert(ProjectPaper)
            .values(project_id=project_id, paper_id=uuid.UUID(p["id"]))
            .on_conflict_do_nothing()
        )

    await session.flush()

    # Passages
    for pa in data.get("passages", []):
        session.add(Passage(
            id=uuid.UUID(pa["id"]),
            paper_id=uuid.UUID(pa["paper_id"]),
            sentence_ids=[uuid.UUID(s) for s in pa.get("sentence_ids", [])],
            verbatim_text=pa["verbatim_text"],
            verified=pa.get("verified", False),
            verified_at=_dt(pa.get("verified_at")),
            checker_model=pa.get("checker_model"),
        ))

    # Syntheses
    for s in data.get("syntheses", []):
        session.add(Synthesis(
            id=uuid.UUID(s["id"]),
            project_id=project_id,
            claim_text=s["claim_text"],
            synthesis_text=s.get("synthesis_text"),
            query_expansion_json=s.get("query_expansion_json"),
            retrieval_corpus=s.get("retrieval_corpus", "project"),
            model=s.get("model", ""),
            checker_model=s.get("checker_model"),
            verification_rate=s.get("verification_rate"),
            status=s.get("status", "done"),
        ))
        for e in s.get("evidence", []):
            session.add(SynthesisEvidence(
                synthesis_id=uuid.UUID(s["id"]),
                passage_id=uuid.UUID(e["passage_id"]),
                role=e["role"],
                ord=e["ord"],
            ))

    # Bricks (insert first, then edges that reference other bricks)
    for b in data.get("bricks", []):
        session.add(Brick(
            id=uuid.UUID(b["id"]),
            project_id=project_id,
            author_id=_u(b.get("author_id")),
            type=b["type"],
            status=b["status"],
            assertion=b["assertion"],
            built_from_synthesis_id=_u(b.get("built_from_synthesis_id")),
            cemented_at=_dt(b.get("cemented_at")),
        ))

    await session.flush()

    for b in data.get("bricks", []):
        brick_id = uuid.UUID(b["id"])
        for r in b.get("rests_on", []):
            session.add(BrickRestsOn(
                brick_id=brick_id,
                target_kind=r["target_kind"],
                target_id=uuid.UUID(r["target_id"]),
                role=r["role"],
            ))
        for bo in b.get("builds_on", []):
            session.add(BrickBuildsOn(
                brick_id=brick_id,
                parent_brick_id=uuid.UUID(bo["parent_brick_id"]),
                ord=bo["ord"],
            ))

    # Annotations
    for a in data.get("annotations", []):
        session.add(Annotation(
            id=uuid.UUID(a["id"]),
            project_id=project_id,
            author_id=uuid.UUID(a["author_id"]),
            target_kind=a["target_kind"],
            target_id=uuid.UUID(a["target_id"]),
            body=a["body"],
        ))

    # Backward-compat: v1 archives exported questions as a separate list.
    # Import them as bricks with type='question'.
    for q in data.get("questions", []):
        session.add(Brick(
            id=uuid.UUID(q["id"]),
            project_id=project_id,
            author_id=_u(q.get("author_id")),
            type="question",
            status=q.get("status", "open"),
            assertion=q["body"],
            built_from_synthesis_id=None,
        ))

    # Evaluations
    for e in data.get("evaluations", []):
        session.add(Evaluation(
            id=uuid.UUID(e["id"]),
            synthesis_id=uuid.UUID(e["synthesis_id"]),
            passage_id=uuid.UUID(e["passage_id"]),
            user_id=uuid.UUID(e["user_id"]),
            stance=e["stance"],
            body=e.get("body"),
        ))

    # Datasets
    for d in data.get("datasets", []):
        session.add(Dataset(
            id=uuid.UUID(d["id"]),
            project_id=project_id,
            kind=d["kind"],
            title=d["title"],
            storage_uri=d.get("storage_uri"),
        ))

    # Event log (preserves project_seq when present; otherwise assigns sequentially)
    raw_events = data.get("provenance", [])
    next_seq = 1
    for p in raw_events:
        seq = p.get("project_seq") or next_seq
        next_seq = max(next_seq, seq + 1)
        session.add(Event(
            id=uuid.UUID(p["id"]),
            project_id=project_id,
            project_seq=seq,
            actor_id=_u(p.get("actor_id")),
            kind=p["kind"],
            entity_kind=p.get("entity_kind") or "unknown",
            entity_id=_u(p.get("entity_id")),
            prev_json=p.get("prev_json"),
            payload_json=p["payload_json"],
        ))
    if raw_events:
        session.add(ProjectEventSeq(project_id=project_id, next_seq=next_seq))

    await session.commit()
    return project_id
