"""Shared fixtures for tests that need a real database.

Assumes the schema is already migrated (`alembic upgrade head`) against
whatever DATABASE_URL points at — CI provisions this via a
`pgvector/pgvector:pg16` service container (see .github/workflows/ci.yml);
locally it's the same docker-compose Postgres used for dev. Tests that don't
need a database (most of the suite) are unaffected — these fixtures are
opt-in via the `db_session`/`client` parameters.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from athenaeum.config import get_settings


@asynccontextmanager
async def _offline_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The app's startup with every outside service absent: the state the
    real lifespan leaves when Redis, Neo4j and Chromium are all unavailable."""
    app.state.arq_pool = None
    app.state.neo4j_driver = None
    app.state.pw = None
    app.state.pw_browser = None
    yield


@pytest.fixture(autouse=True, scope="session")
def _no_outside_services_at_app_startup() -> Iterator[None]:
    """Every `with TestClient(app)` runs the app's lifespan. The real one
    connects to Redis (retrying for seconds when it's down), Neo4j, and
    launches Chromium, once per test. With only Postgres up (as in CI) that
    made the suite crawl, and with the full stack up tests saw different
    state than without it. No test relies on those connections: tests that
    need a queue or driver pass their own."""
    from athenaeum.main import app

    real = app.router.lifespan_context
    app.router.lifespan_context = _offline_lifespan
    yield
    app.router.lifespan_context = real


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session isolated from every other test.

    Code under test (endpoints, `import_project`, ...) sometimes calls
    `session.commit()` directly. A fixture that just opens a transaction and
    never commits would break the moment that happens — the inner commit
    would end the transaction early and leave rows visible to whatever runs
    next. `join_transaction_mode="create_savepoint"` makes an inner
    `commit()` release a savepoint instead, so the *outer* transaction (and
    therefore full isolation) survives regardless of what the code under
    test does — it's only ever rolled back here, at teardown.

    The engine is created fresh per test (function-scoped), not shared at
    session scope: asyncpg connections are bound to the event loop that
    created them, and pytest-asyncio gives each test function its own loop
    by default — a session-scoped engine would hand out connections from a
    now-dead loop to later tests and silently break rollback-based
    isolation (this was tried and failed exactly that way).
    """
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            await conn.begin()
            session = AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            try:
                yield session
            finally:
                await session.close()
                await conn.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[TestClient]:
    """A TestClient wired to the same isolated `db_session` every endpoint
    in the test will use, via FastAPI's dependency-override mechanism."""
    from athenaeum.main import app
    from athenaeum.storage.db import get_session

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_session, None)
