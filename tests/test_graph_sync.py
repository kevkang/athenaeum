"""End-to-end test for graph/sync.py against real Postgres + Neo4j.

sync_project_graph() opens its own AsyncSession from a raw AsyncEngine
rather than accepting one — so it can't see data seeded through the
rollback-isolated `db_session` fixture (separate connection, separate
uncommitted transaction). This uses a plain committed engine instead, with
explicit teardown (delete the Postgres rows, delete the matching Neo4j
nodes) rather than rollback-based isolation.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import neo4j
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from athenaeum.config import get_settings
from athenaeum.graph.sync import sync_project_graph
from athenaeum.storage.models import (
    Paper,
    Project,
    ProjectMember,
    ProjectPaper,
    Statement,
    StatementEdge,
    StatementEvidence,
    User,
)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def neo4j_driver() -> AsyncIterator[neo4j.AsyncDriver]:
    settings = get_settings()
    driver = neo4j.AsyncGraphDatabase.driver(
        settings.neo4j_url, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    yield driver
    await driver.close()


async def _cypher(driver: neo4j.AsyncDriver, query: str, **params: Any) -> list[neo4j.Record]:
    async with driver.session() as session:
        result = await session.run(query, **params)
        return [r async for r in result]


async def test_sync_project_graph_covers_every_evidence_kind_and_edge_type(
    pg_engine: AsyncEngine, neo4j_driver: neo4j.AsyncDriver,
) -> None:
    project_id = uuid.uuid4()
    paper1_id = uuid.uuid4()
    paper2_id = uuid.uuid4()
    st1_id, st2_id, st3_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    dataset_ref_id = uuid.uuid4()

    async with AsyncSession(pg_engine, expire_on_commit=False) as s:
        owner = User(id=uuid.uuid4(), orcid=str(uuid.uuid4())[:19], display_name="Sync Test")
        s.add(owner)
        await s.flush()

        s.add(Project(id=project_id, title="Graph Sync Test", owner_id=owner.id))
        await s.flush()
        s.add(ProjectMember(project_id=project_id, user_id=owner.id, role="owner"))

        s.add(Paper(id=paper1_id, title="Paper One", status="full_text"))
        s.add(Paper(id=paper2_id, title="Paper Two", status="full_text"))
        await s.flush()
        s.add(ProjectPaper(project_id=project_id, paper_id=paper1_id, added_by=owner.id))
        s.add(ProjectPaper(project_id=project_id, paper_id=paper2_id, added_by=owner.id))

        s.add(Statement(id=st1_id, project_id=project_id, author_id=owner.id,
                         type="foundation", status="locked", assertion="Statement one."))
        s.add(Statement(id=st2_id, project_id=project_id, author_id=owner.id,
                         type="synthesis", status="draft", assertion="Statement two."))
        s.add(Statement(id=st3_id, project_id=project_id, author_id=owner.id,
                         type="synthesis", status="draft", assertion="Statement three."))
        await s.flush()

        # Evidence covering all 3 target-kind buckets the sync groups by.
        s.add(StatementEvidence(
            statement_id=st1_id, target_kind="paper", target_id=paper1_id, role="theory",
        ))
        s.add(StatementEvidence(
            statement_id=st1_id, target_kind="statement", target_id=st2_id, role="supports",
        ))
        s.add(StatementEvidence(
            statement_id=st1_id, target_kind="dataset", target_id=dataset_ref_id, role="data",
        ))

        # Edges covering all 3 relation types.
        s.add(StatementEdge(
            from_statement_id=st2_id, to_statement_id=st1_id, relation="supports",
        ))
        s.add(StatementEdge(
            from_statement_id=st3_id, to_statement_id=st1_id, relation="tension",
        ))
        s.add(StatementEdge(
            from_statement_id=st3_id, to_statement_id=st2_id, relation="relates_to",
        ))
        await s.commit()

    try:
        await sync_project_graph(neo4j_driver, pg_engine, project_id)

        papers = await _cypher(
            neo4j_driver,
            "MATCH (:Project {id: $pid})-[:HAS_PAPER]->(p:Paper) RETURN p.id AS id",
            pid=str(project_id),
        )
        assert {r["id"] for r in papers} == {str(paper1_id), str(paper2_id)}

        ev_paper = await _cypher(
            neo4j_driver,
            "MATCH (:Statement {id: $sid})-[r:HAS_EVIDENCE]->(t:Paper) "
            "RETURN t.id AS id, r.role AS role",
            sid=str(st1_id),
        )
        assert [dict(r) for r in ev_paper] == [{"id": str(paper1_id), "role": "theory"}]

        ev_stmt = await _cypher(
            neo4j_driver,
            "MATCH (:Statement {id: $sid})-[r:HAS_EVIDENCE]->(t:Statement) "
            "RETURN t.id AS id, r.role AS role",
            sid=str(st1_id),
        )
        assert [dict(r) for r in ev_stmt] == [{"id": str(st2_id), "role": "supports"}]

        ev_other = await _cypher(
            neo4j_driver,
            "MATCH (:Statement {id: $sid})-[r:HAS_EVIDENCE]->(t:Artifact) "
            "RETURN t.id AS id, t.kind AS kind, r.role AS role",
            sid=str(st1_id),
        )
        assert [dict(r) for r in ev_other] == [
            {"id": str(dataset_ref_id), "kind": "dataset", "role": "data"}
        ]

        expected_edges = [
            (st2_id, st1_id, "SUPPORTS"),
            (st3_id, st1_id, "IN_TENSION"),
            (st3_id, st2_id, "RELATES_TO"),
        ]
        for from_id, to_id, rel in expected_edges:
            rows = await _cypher(
                neo4j_driver,
                f"MATCH (a:Statement {{id: $from_id}})-[:{rel}]->(b:Statement {{id: $to_id}}) "
                "RETURN count(*) AS c",
                from_id=str(from_id), to_id=str(to_id),
            )
            assert rows[0]["c"] == 1, f"missing {rel} edge {from_id}->{to_id}"

        # Mutate in Postgres — drop one evidence row and one edge — then
        # re-sync. Proves both idempotency (no duplicate nodes/edges from
        # re-running) and that stale Neo4j edges actually get cleared, not
        # just accumulated.
        async with AsyncSession(pg_engine, expire_on_commit=False) as s:
            await s.execute(delete(StatementEvidence).where(
                StatementEvidence.statement_id == st1_id,
                StatementEvidence.target_kind == "dataset",
            ))
            await s.execute(delete(StatementEdge).where(
                StatementEdge.from_statement_id == st3_id,
                StatementEdge.to_statement_id == st2_id,
            ))
            await s.commit()

        await sync_project_graph(neo4j_driver, pg_engine, project_id)

        ev_other_after = await _cypher(
            neo4j_driver,
            "MATCH (:Statement {id: $sid})-[r:HAS_EVIDENCE]->(t:Artifact) RETURN t.id AS id",
            sid=str(st1_id),
        )
        assert ev_other_after == []

        ev_paper_after = await _cypher(
            neo4j_driver,
            "MATCH (:Statement {id: $sid})-[r:HAS_EVIDENCE]->(t:Paper) RETURN count(*) AS c",
            sid=str(st1_id),
        )
        assert ev_paper_after[0]["c"] == 1  # still there, not duplicated

        relates_after = await _cypher(
            neo4j_driver,
            "MATCH (a:Statement {id: $from_id})-[:RELATES_TO]->(b:Statement {id: $to_id}) "
            "RETURN count(*) AS c",
            from_id=str(st3_id), to_id=str(st2_id),
        )
        assert relates_after[0]["c"] == 0

        supports_after = await _cypher(
            neo4j_driver,
            "MATCH (a:Statement {id: $from_id})-[:SUPPORTS]->(b:Statement {id: $to_id}) "
            "RETURN count(*) AS c",
            from_id=str(st2_id), to_id=str(st1_id),
        )
        assert supports_after[0]["c"] == 1  # still there, not duplicated
    finally:
        # Explicit cleanup on both sides — this fixture is commit-based, not
        # rollback-isolated, so nothing rolls this back automatically.
        await _cypher(
            neo4j_driver,
            "MATCH (n) WHERE n.id IN $ids DETACH DELETE n",
            ids=[
                str(project_id), str(paper1_id), str(paper2_id),
                str(st1_id), str(st2_id), str(st3_id),
            ],
        )
        async with AsyncSession(pg_engine, expire_on_commit=False) as s:
            await s.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await s.execute(delete(Project).where(Project.id == project_id))
            await s.execute(delete(Paper).where(Paper.id.in_([paper1_id, paper2_id])))
            await s.commit()
