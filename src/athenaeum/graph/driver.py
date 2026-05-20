"""Neo4j async driver factory and schema initialisation."""
from __future__ import annotations

import neo4j
import structlog

log = structlog.get_logger()

_CONSTRAINTS = [
    "CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT brick_id IF NOT EXISTS FOR (b:Brick) REQUIRE b.id IS UNIQUE",
    "CREATE CONSTRAINT project_id IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
]


def make_driver(url: str, user: str, password: str) -> neo4j.AsyncDriver:
    return neo4j.AsyncGraphDatabase.driver(url, auth=(user, password))


async def init_constraints(driver: neo4j.AsyncDriver) -> None:
    async with driver.session() as session:
        for cql in _CONSTRAINTS:
            try:
                await session.run(cql)
            except Exception as exc:
                log.warning("neo4j.constraint_skip", error=str(exc))
