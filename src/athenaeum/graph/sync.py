"""Postgres → Neo4j sync helpers (all MERGE-based, idempotent).

upsert_paper() and sync_paper_cites() stay single-item — worker/jobs.py
calls them as each paper/citation is individually discovered during
ingestion, where there's nothing to batch. sync_project_graph() is the
batch path (a full per-project re-sync), and does batch: one UNWIND-based
Cypher statement per node/edge *type* regardless of how many papers or
statements the project has, instead of one round trip per row.
"""
from __future__ import annotations

import uuid
from typing import Any

import neo4j
import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.storage.models import (
    Paper,
    Project,
    ProjectPaper,
    Statement,
    StatementEdge,
    StatementEvidence,
)

log = structlog.get_logger()

# statement_edges.relation -> Neo4j relationship type
_EDGE_RELATION_TYPES = {
    "relates_to": "RELATES_TO",
    "supports": "SUPPORTS",
    "tension": "IN_TENSION",
}


async def upsert_paper(session: neo4j.AsyncSession, paper: Paper) -> None:
    await session.run(
        """
        MERGE (p:Paper {id: $id})
        SET p.doi        = $doi,
            p.title      = $title,
            p.year       = $year,
            p.venue      = $venue,
            p.openalex_id = $openalex_id
        """,
        id=str(paper.id),
        doi=paper.doi,
        title=paper.title,
        year=paper.year,
        venue=paper.venue,
        openalex_id=paper.openalex_id,
    )


async def sync_paper_cites(
    session: neo4j.AsyncSession,
    from_paper_id: str,
    to_paper_ids: list[str],
) -> None:
    for to_id in to_paper_ids:
        await session.run(
            """
            MATCH (a:Paper {id: $from_id})
            MATCH (b:Paper {id: $to_id})
            MERGE (a)-[:CITES]->(b)
            """,
            from_id=from_paper_id,
            to_id=to_id,
        )


async def _upsert_papers_batch(
    neo_session: neo4j.AsyncSession, project_id: str, papers: list[Paper]
) -> None:
    if not papers:
        return
    await neo_session.run(
        """
        UNWIND $rows AS row
        MERGE (p:Paper {id: row.id})
        SET p.doi = row.doi, p.title = row.title, p.year = row.year,
            p.venue = row.venue, p.openalex_id = row.openalex_id
        WITH p
        MATCH (proj:Project {id: $project_id})
        MERGE (proj)-[:HAS_PAPER]->(p)
        """,
        project_id=project_id,
        rows=[
            {
                "id": str(p.id), "doi": p.doi, "title": p.title,
                "year": p.year, "venue": p.venue, "openalex_id": p.openalex_id,
            }
            for p in papers
        ],
    )


async def _upsert_statements_batch(
    neo_session: neo4j.AsyncSession, statements: list[Statement]
) -> None:
    if not statements:
        return
    await neo_session.run(
        """
        UNWIND $rows AS row
        MERGE (s:Statement {id: row.id})
        SET s.project_id = row.project_id, s.type = row.type,
            s.status = row.status, s.assertion = row.assertion
        """,
        rows=[
            {
                "id": str(s.id), "project_id": str(s.project_id),
                "type": s.type, "status": s.status, "assertion": s.assertion,
            }
            for s in statements
        ],
    )


async def _sync_all_statement_evidence(
    neo_session: neo4j.AsyncSession,
    statement_ids: list[str],
    evidence_rows: list[StatementEvidence],
) -> None:
    """Clears every statement's outgoing HAS_EVIDENCE edges and re-creates
    them from `evidence_rows`, in three UNWIND batches (grouped by target
    node label — Cypher can't parameterize a node's label any more than it
    can a relationship type) rather than one round trip per evidence row."""
    if not statement_ids:
        return
    await neo_session.run(
        """
        UNWIND $sids AS sid
        MATCH (s:Statement {id: sid})-[r:HAS_EVIDENCE]->()
        DELETE r
        """,
        sids=statement_ids,
    )

    by_kind: dict[str, list[dict[str, Any]]] = {"paper": [], "statement": [], "other": []}
    for ev in evidence_rows:
        bucket = ev.target_kind if ev.target_kind in ("paper", "statement") else "other"
        by_kind[bucket].append({
            "sid": str(ev.statement_id), "tid": str(ev.target_id),
            "role": ev.role, "kind": ev.target_kind,
        })

    if by_kind["paper"]:
        await neo_session.run(
            """
            UNWIND $rows AS row
            MATCH (s:Statement {id: row.sid})
            MERGE (t:Paper {id: row.tid})
            MERGE (s)-[:HAS_EVIDENCE {role: row.role, target_kind: row.kind}]->(t)
            """,
            rows=by_kind["paper"],
        )
    if by_kind["statement"]:
        await neo_session.run(
            """
            UNWIND $rows AS row
            MATCH (s:Statement {id: row.sid})
            MATCH (t:Statement {id: row.tid})
            MERGE (s)-[:HAS_EVIDENCE {role: row.role, target_kind: row.kind}]->(t)
            """,
            rows=by_kind["statement"],
        )
    if by_kind["other"]:
        await neo_session.run(
            """
            UNWIND $rows AS row
            MATCH (s:Statement {id: row.sid})
            MERGE (t:Artifact {id: row.tid, kind: row.kind})
            MERGE (s)-[:HAS_EVIDENCE {role: row.role, target_kind: row.kind}]->(t)
            """,
            rows=by_kind["other"],
        )


async def _sync_all_statement_edges(
    neo_session: neo4j.AsyncSession,
    statement_ids: list[str],
    edge_rows: list[StatementEdge],
) -> None:
    """Same batching approach as evidence, grouped by relationship type
    instead of target label. `edge_rows` is filtered to edges *originating*
    from one of `statement_ids` — matching the original per-statement sync,
    which only ever managed a statement's outgoing edges; an edge whose
    from_statement_id belongs to a different project is left for that
    project's own sync to handle."""
    if not statement_ids:
        return
    await neo_session.run(
        """
        UNWIND $sids AS sid
        MATCH (s:Statement {id: sid})-[r:RELATES_TO|SUPPORTS|IN_TENSION]->()
        DELETE r
        """,
        sids=statement_ids,
    )

    statement_id_set = set(statement_ids)
    by_type: dict[str, list[dict[str, str]]] = {}
    for edge in edge_rows:
        if str(edge.from_statement_id) not in statement_id_set:
            continue
        rel_type = _EDGE_RELATION_TYPES.get(edge.relation, "RELATES_TO")
        by_type.setdefault(rel_type, []).append({
            "from_id": str(edge.from_statement_id), "to_id": str(edge.to_statement_id),
        })

    for rel_type, rows in by_type.items():
        await neo_session.run(
            f"""
            UNWIND $rows AS row
            MATCH (a:Statement {{id: row.from_id}})
            MATCH (b:Statement {{id: row.to_id}})
            MERGE (a)-[:{rel_type}]->(b)
            """,
            rows=rows,
        )


async def sync_project_graph(
    driver: neo4j.AsyncDriver,
    engine: AsyncEngine,
    project_id: uuid.UUID,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as pg:
        project = await pg.get(Project, project_id)
        if project is None:
            return

        papers = list(await pg.scalars(
            select(Paper)
            .join(ProjectPaper, Paper.id == ProjectPaper.paper_id)
            .where(ProjectPaper.project_id == project_id)
        ))
        statements = list(await pg.scalars(
            select(Statement).where(Statement.project_id == project_id)
        ))

        statement_ids = [s.id for s in statements]
        evidence_rows: list[StatementEvidence] = []
        edge_rows: list[StatementEdge] = []
        if statement_ids:
            evidence_rows = list(await pg.scalars(
                select(StatementEvidence).where(StatementEvidence.statement_id.in_(statement_ids))
            ))
            edge_rows = list(await pg.scalars(
                select(StatementEdge).where(
                    or_(
                        StatementEdge.from_statement_id.in_(statement_ids),
                        StatementEdge.to_statement_id.in_(statement_ids),
                    )
                )
            ))

    statement_id_strs = [str(sid) for sid in statement_ids]

    async with driver.session() as neo:
        await neo.run(
            "MERGE (p:Project {id: $id}) SET p.title = $title",
            id=str(project_id), title=project.title,
        )
        await _upsert_papers_batch(neo, str(project_id), papers)
        await _upsert_statements_batch(neo, statements)
        await _sync_all_statement_evidence(neo, statement_id_strs, evidence_rows)
        await _sync_all_statement_edges(neo, statement_id_strs, edge_rows)

    log.info(
        "neo4j.sync_done",
        project_id=str(project_id),
        papers=len(papers),
        statements=len(statements),
    )
