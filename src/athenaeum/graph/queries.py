"""Read-only Cypher queries against the Neo4j derived graph."""
from __future__ import annotations

import neo4j


async def ghost_papers(driver: neo4j.AsyncDriver, project_id: str) -> list[dict]:
    """Papers referenced in brick RESTS_ON edges but not added to the project."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (b:Brick {project_id: $pid})-[r:RESTS_ON]->(p:Paper)
            WHERE r.target_kind = 'paper'
              AND NOT EXISTS {
                  MATCH (:Project {id: $pid})-[:HAS_PAPER]->(p)
              }
            RETURN DISTINCT
                p.id    AS id,
                p.doi   AS doi,
                p.title AS title,
                p.year  AS year,
                p.venue AS venue
            """,
            pid=project_id,
        )
        return await result.data()


async def co_citation_neighbors(
    driver: neo4j.AsyncDriver,
    paper_id: str,
    limit: int = 20,
) -> list[dict]:
    """Papers that share at least one cited work with the given paper, ranked by shared count."""
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (p:Paper {id: $paper_id})-[:CITES]->(shared:Paper)<-[:CITES]-(neighbor:Paper)
            WHERE neighbor.id <> $paper_id
            WITH neighbor, count(shared) AS shared_count
            ORDER BY shared_count DESC
            LIMIT toInteger($limit)
            RETURN
                neighbor.id    AS id,
                neighbor.doi   AS doi,
                neighbor.title AS title,
                neighbor.year  AS year,
                neighbor.venue AS venue,
                shared_count
            """,
            paper_id=paper_id,
            limit=limit,
        )
        return await result.data()
