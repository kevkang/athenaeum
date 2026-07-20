"""Reconstruct a project from a .ath archive dict."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.storage.models import (
    Annotation,
    Dataset,
    Evaluation,
    Paper,
    Passage,
    Project,
    ProjectMember,
    ProjectPaper,
    ProvenanceLog,
    Statement,
    StatementEdge,
    StatementEvidence,
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

    # Statements (insert first, then edges that reference other statements).
    # "bricks"/"rests_on"/"builds_on" are accepted as a fallback for archives
    # exported before the bricks -> statements rename; "cemented"/"cemented_at"
    # similarly for archives exported before the cement -> lock rename.
    raw_statements = data.get("statements", data.get("bricks", []))
    for s in raw_statements:
        status = "locked" if s["status"] == "cemented" else s["status"]
        session.add(Statement(
            id=uuid.UUID(s["id"]),
            project_id=project_id,
            author_id=_u(s.get("author_id")),
            type=s["type"],
            status=status,
            assertion=s["assertion"],
            built_from_synthesis_id=_u(s.get("built_from_synthesis_id")),
            locked_at=_dt(s.get("locked_at", s.get("cemented_at"))),
        ))

    await session.flush()

    for s in raw_statements:
        statement_id = uuid.UUID(s["id"])
        for r in s.get("evidence", s.get("rests_on", [])):
            target_kind = "statement" if r["target_kind"] == "brick" else r["target_kind"]
            session.add(StatementEvidence(
                statement_id=statement_id,
                target_kind=target_kind,
                target_id=uuid.UUID(r["target_id"]),
                role=r["role"],
            ))
        for e in s.get("edges", []):
            session.add(StatementEdge(
                from_statement_id=statement_id,
                to_statement_id=uuid.UUID(e["to_statement_id"]),
                relation=e["relation"],
            ))
        # Old-format builds_on has no equivalent typed relation — best-effort
        # map it to a neutral "relates_to" edge (the DAG-ordering concept
        # itself, `ord`, no longer exists in the new edge model).
        for bo in s.get("builds_on", []):
            session.add(StatementEdge(
                from_statement_id=statement_id,
                to_statement_id=uuid.UUID(bo["parent_brick_id"]),
                relation="relates_to",
            ))

    # Annotations
    for a in data.get("annotations", []):
        target_kind = "statement" if a["target_kind"] == "brick" else a["target_kind"]
        session.add(Annotation(
            id=uuid.UUID(a["id"]),
            project_id=project_id,
            author_id=uuid.UUID(a["author_id"]),
            target_kind=target_kind,
            target_id=uuid.UUID(a["target_id"]),
            body=a["body"],
        ))

    # Backward-compat: v1 archives exported questions as a separate list.
    # Import them as statements with type='question'.
    for q in data.get("questions", []):
        session.add(Statement(
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

    # Provenance log
    for p in data.get("provenance", []):
        session.add(ProvenanceLog(
            id=uuid.UUID(p["id"]),
            project_id=project_id,
            kind=p["kind"],
            payload_json=p["payload_json"],
        ))

    await session.commit()
    return project_id
