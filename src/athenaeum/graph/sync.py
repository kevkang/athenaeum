"""Postgres → Neo4j sync helpers (all MERGE-based, idempotent)."""
from __future__ import annotations

import uuid

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


async def _sync_statement(neo_session: neo4j.AsyncSession, statement: Statement) -> None:
    sid = str(statement.id)
    await neo_session.run(
        """
        MERGE (s:Statement {id: $id})
        SET s.project_id = $project_id,
            s.type       = $type,
            s.status     = $status,
            s.assertion  = $assertion
        """,
        id=sid,
        project_id=str(statement.project_id),
        type=statement.type,
        status=statement.status,
        assertion=statement.assertion,
    )


async def _sync_statement_evidence(
    neo_session: neo4j.AsyncSession,
    sid: str,
    evidence: list[StatementEvidence],
) -> None:
    await neo_session.run(
        "MATCH (s:Statement {id: $id})-[r:HAS_EVIDENCE]->() DELETE r",
        id=sid,
    )
    for ev in evidence:
        tid = str(ev.target_id)
        tk = ev.target_kind
        if tk == "paper":
            await neo_session.run(
                """
                MATCH (s:Statement {id: $sid})
                MERGE (t:Paper {id: $tid})
                MERGE (s)-[:HAS_EVIDENCE {role: $role, target_kind: $kind}]->(t)
                """,
                sid=sid, tid=tid, role=ev.role, kind=tk,
            )
        elif tk == "statement":
            await neo_session.run(
                """
                MATCH (s:Statement {id: $sid})
                MATCH (t:Statement {id: $tid})
                MERGE (s)-[:HAS_EVIDENCE {role: $role, target_kind: $kind}]->(t)
                """,
                sid=sid, tid=tid, role=ev.role, kind=tk,
            )
        else:
            await neo_session.run(
                """
                MATCH (s:Statement {id: $sid})
                MERGE (t:Artifact {id: $tid, kind: $kind})
                MERGE (s)-[:HAS_EVIDENCE {role: $role, target_kind: $kind}]->(t)
                """,
                sid=sid, tid=tid, role=ev.role, kind=tk,
            )


async def _sync_statement_edges(
    neo_session: neo4j.AsyncSession,
    sid: str,
    edges: list[StatementEdge],
) -> None:
    await neo_session.run(
        "MATCH (s:Statement {id: $id})-[r:RELATES_TO|SUPPORTS|IN_TENSION]-() DELETE r",
        id=sid,
    )
    for edge in edges:
        rel_type = _EDGE_RELATION_TYPES.get(edge.relation, "RELATES_TO")
        await neo_session.run(
            f"""
            MATCH (a:Statement {{id: $from_id}})
            MATCH (b:Statement {{id: $to_id}})
            MERGE (a)-[:{rel_type}]->(b)
            """,
            from_id=str(edge.from_statement_id),
            to_id=str(edge.to_statement_id),
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

    evidence_by_statement: dict[str, list[StatementEvidence]] = {}
    for ev in evidence_rows:
        evidence_by_statement.setdefault(str(ev.statement_id), []).append(ev)
    edges_by_statement: dict[str, list[StatementEdge]] = {}
    for edge in edge_rows:
        edges_by_statement.setdefault(str(edge.from_statement_id), []).append(edge)

    async with driver.session() as neo:
        await neo.run(
            "MERGE (p:Project {id: $id}) SET p.title = $title",
            id=str(project_id), title=project.title,
        )
        for paper in papers:
            await upsert_paper(neo, paper)
            await neo.run(
                """
                MATCH (proj:Project {id: $pid})
                MATCH (p:Paper {id: $paper_id})
                MERGE (proj)-[:HAS_PAPER]->(p)
                """,
                pid=str(project_id), paper_id=str(paper.id),
            )
        for statement in statements:
            sid = str(statement.id)
            await _sync_statement(neo, statement)
            await _sync_statement_evidence(neo, sid, evidence_by_statement.get(sid, []))
            await _sync_statement_edges(neo, sid, edges_by_statement.get(sid, []))

    log.info(
        "neo4j.sync_done",
        project_id=str(project_id),
        papers=len(papers),
        statements=len(statements),
    )
