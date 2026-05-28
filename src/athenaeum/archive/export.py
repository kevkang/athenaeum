"""Build and serialise a project archive (.ath format = YAML v1)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import StringIO
from typing import Any

from ruamel.yaml import YAML
from sqlalchemy import select
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
    ProjectPaper,
    ProvenanceLog,
    Synthesis,
    SynthesisEvidence,
)

ATH_VERSION = 1


def _s(v: uuid.UUID | None) -> str | None:
    return str(v) if v is not None else None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def build_archive(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    papers = list(await session.scalars(
        select(Paper)
        .join(ProjectPaper, Paper.id == ProjectPaper.paper_id)
        .where(ProjectPaper.project_id == project_id)
    ))
    paper_ids = [p.id for p in papers]

    passages: list[Passage] = [] if not paper_ids else list(await session.scalars(
        select(Passage).where(Passage.paper_id.in_(paper_ids))
    ))

    syntheses = list(await session.scalars(
        select(Synthesis).where(Synthesis.project_id == project_id)
    ))
    synthesis_ids = [s.id for s in syntheses]

    evidence: list[SynthesisEvidence] = [] if not synthesis_ids else list(await session.scalars(
        select(SynthesisEvidence).where(SynthesisEvidence.synthesis_id.in_(synthesis_ids))
    ))

    bricks = list(await session.scalars(
        select(Brick).where(Brick.project_id == project_id)
    ))
    brick_ids = [b.id for b in bricks]

    rests_on: list[BrickRestsOn] = [] if not brick_ids else list(await session.scalars(
        select(BrickRestsOn).where(BrickRestsOn.brick_id.in_(brick_ids))
    ))
    builds_on: list[BrickBuildsOn] = [] if not brick_ids else list(await session.scalars(
        select(BrickBuildsOn).where(BrickBuildsOn.brick_id.in_(brick_ids))
    ))

    annotations = list(await session.scalars(
        select(Annotation).where(Annotation.project_id == project_id)
    ))
    evaluations: list[Evaluation] = [] if not synthesis_ids else list(await session.scalars(
        select(Evaluation).where(Evaluation.synthesis_id.in_(synthesis_ids))
    ))
    datasets = list(await session.scalars(
        select(Dataset).where(Dataset.project_id == project_id)
    ))
    provenance = list(await session.scalars(
        select(ProvenanceLog)
        .where(ProvenanceLog.project_id == project_id)
        .order_by(ProvenanceLog.created_at)
    ))

    # Group by parent for embedding
    ev_by_synth: dict[uuid.UUID, list[SynthesisEvidence]] = {}
    for e in evidence:
        ev_by_synth.setdefault(e.synthesis_id, []).append(e)

    ro_by_brick: dict[uuid.UUID, list[BrickRestsOn]] = {}
    for r in rests_on:
        ro_by_brick.setdefault(r.brick_id, []).append(r)

    bo_by_brick: dict[uuid.UUID, list[BrickBuildsOn]] = {}
    for b in builds_on:
        bo_by_brick.setdefault(b.brick_id, []).append(b)

    return {
        "ath_version": ATH_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "id": str(project.id),
            "title": project.title,
            "description": project.description,
            "license": project.license,
        },
        "papers": [
            {
                "id": str(p.id),
                "doi": p.doi,
                "openalex_id": p.openalex_id,
                "title": p.title,
                "authors_json": p.authors_json,
                "venue": p.venue,
                "year": p.year,
                "abstract": p.abstract,
                "status": p.status,
                "created_at": _iso(p.created_at),
            }
            for p in papers
        ],
        "passages": [
            {
                "id": str(pa.id),
                "paper_id": str(pa.paper_id),
                "sentence_ids": [str(s) for s in (pa.sentence_ids or [])],
                "verbatim_text": pa.verbatim_text,
                "verified": pa.verified,
                "verified_at": _iso(pa.verified_at),
                "checker_model": pa.checker_model,
            }
            for pa in passages
        ],
        "syntheses": [
            {
                "id": str(s.id),
                "claim_text": s.claim_text,
                "synthesis_text": s.synthesis_text,
                "retrieval_corpus": s.retrieval_corpus,
                "model": s.model,
                "checker_model": s.checker_model,
                "verification_rate": s.verification_rate,
                "status": s.status,
                "run_at": _iso(s.run_at),
                "query_expansion_json": s.query_expansion_json,
                "evidence": [
                    {
                        "passage_id": str(e.passage_id),
                        "role": e.role,
                        "ord": e.ord,
                    }
                    for e in sorted(ev_by_synth.get(s.id, []), key=lambda x: x.ord)
                ],
            }
            for s in syntheses
        ],
        "bricks": [
            {
                "id": str(b.id),
                "author_id": _s(b.author_id),
                "type": b.type,
                "status": b.status,
                "assertion": b.assertion,
                "built_from_synthesis_id": _s(b.built_from_synthesis_id),
                "cemented_at": _iso(b.cemented_at),
                "drafted_at": _iso(b.drafted_at),
                "rests_on": [
                    {
                        "target_kind": r.target_kind,
                        "target_id": str(r.target_id),
                        "role": r.role,
                    }
                    for r in ro_by_brick.get(b.id, [])
                ],
                "builds_on": [
                    {
                        "parent_brick_id": str(r.parent_brick_id),
                        "ord": r.ord,
                    }
                    for r in sorted(bo_by_brick.get(b.id, []), key=lambda x: x.ord)
                ],
            }
            for b in bricks
        ],
        "annotations": [
            {
                "id": str(a.id),
                "author_id": str(a.author_id),
                "target_kind": a.target_kind,
                "target_id": str(a.target_id),
                "body": a.body,
                "created_at": _iso(a.created_at),
            }
            for a in annotations
        ],
        "evaluations": [
            {
                "id": str(e.id),
                "synthesis_id": str(e.synthesis_id),
                "passage_id": str(e.passage_id),
                "user_id": str(e.user_id),
                "stance": e.stance,
                "body": e.body,
                "created_at": _iso(e.created_at),
            }
            for e in evaluations
        ],
        "datasets": [
            {
                "id": str(d.id),
                "kind": d.kind,
                "title": d.title,
                "storage_uri": d.storage_uri,
                "created_at": _iso(d.created_at),
            }
            for d in datasets
        ],
        "provenance": [
            {
                "id": str(p.id),
                "kind": p.kind,
                "payload_json": p.payload_json,
                "created_at": _iso(p.created_at),
            }
            for p in provenance
        ],
    }


def dumps(data: dict[str, Any]) -> str:
    y = YAML()
    y.default_flow_style = False
    y.width = 120
    y.indent(mapping=2, sequence=4, offset=2)
    buf = StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def loads(text: str) -> dict[str, Any]:
    y = YAML()
    return dict(y.load(text))
