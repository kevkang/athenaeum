"""Postgres → Neo4j sync helpers (all MERGE-based, idempotent)."""
from __future__ import annotations

import uuid

import neo4j
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.storage.models import (
    Brick,
    BrickBuildsOn,
    BrickRestsOn,
    Paper,
    Project,
    ProjectPaper,
)

log = structlog.get_logger()


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


async def _sync_brick(neo_session: neo4j.AsyncSession, brick: Brick) -> None:
    bid = str(brick.id)
    await neo_session.run(
        """
        MERGE (b:Brick {id: $id})
        SET b.project_id = $project_id,
            b.type       = $type,
            b.status     = $status,
            b.assertion  = $assertion
        """,
        id=bid,
        project_id=str(brick.project_id),
        type=brick.type,
        status=brick.status,
        assertion=brick.assertion,
    )


async def _sync_brick_edges(
    neo_session: neo4j.AsyncSession,
    bid: str,
    rests_on: list[BrickRestsOn],
    builds_on: list[BrickBuildsOn],
) -> None:
    await neo_session.run(
        "MATCH (b:Brick {id: $id})-[r:RESTS_ON|BUILDS_ON]->() DELETE r",
        id=bid,
    )
    for edge in rests_on:
        tid = str(edge.target_id)
        tk = edge.target_kind
        if tk == "paper":
            await neo_session.run(
                """
                MATCH (b:Brick {id: $bid})
                MERGE (t:Paper {id: $tid})
                MERGE (b)-[:RESTS_ON {role: $role, target_kind: $kind}]->(t)
                """,
                bid=bid, tid=tid, role=edge.role, kind=tk,
            )
        elif tk == "brick":
            await neo_session.run(
                """
                MATCH (b:Brick {id: $bid})
                MATCH (t:Brick {id: $tid})
                MERGE (b)-[:RESTS_ON {role: $role, target_kind: $kind}]->(t)
                """,
                bid=bid, tid=tid, role=edge.role, kind=tk,
            )
        else:
            await neo_session.run(
                """
                MATCH (b:Brick {id: $bid})
                MERGE (t:Artifact {id: $tid, kind: $kind})
                MERGE (b)-[:RESTS_ON {role: $role, target_kind: $kind}]->(t)
                """,
                bid=bid, tid=tid, role=edge.role, kind=tk,
            )
    for edge in builds_on:
        await neo_session.run(
            """
            MATCH (b:Brick {id: $bid})
            MATCH (p:Brick  {id: $pid})
            MERGE (b)-[:BUILDS_ON {ord: $ord}]->(p)
            """,
            bid=bid,
            pid=str(edge.parent_brick_id),
            ord=edge.ord,
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
        bricks = list(await pg.scalars(
            select(Brick).where(Brick.project_id == project_id)
        ))

        brick_ids = [b.id for b in bricks]
        rests_on_rows: list[BrickRestsOn] = []
        builds_on_rows: list[BrickBuildsOn] = []
        if brick_ids:
            rests_on_rows = list(await pg.scalars(
                select(BrickRestsOn).where(BrickRestsOn.brick_id.in_(brick_ids))
            ))
            builds_on_rows = list(await pg.scalars(
                select(BrickBuildsOn).where(BrickBuildsOn.brick_id.in_(brick_ids))
            ))

    rests_by_brick: dict[str, list[BrickRestsOn]] = {}
    for r in rests_on_rows:
        rests_by_brick.setdefault(str(r.brick_id), []).append(r)
    builds_by_brick: dict[str, list[BrickBuildsOn]] = {}
    for r in builds_on_rows:
        builds_by_brick.setdefault(str(r.brick_id), []).append(r)

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
        for brick in bricks:
            bid = str(brick.id)
            await _sync_brick(neo, brick)
            await _sync_brick_edges(
                neo, bid,
                rests_by_brick.get(bid, []),
                builds_by_brick.get(bid, []),
            )

    log.info(
        "neo4j.sync_done",
        project_id=str(project_id),
        papers=len(papers),
        bricks=len(bricks),
    )
