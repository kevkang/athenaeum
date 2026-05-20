from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from athenaeum import __version__
from athenaeum.api import annotations, archive, auth, bricks, graph, members, papers, projects, public, search, syntheses
from athenaeum.config import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        log.info("arq_pool.connected")
    except Exception as exc:
        log.warning("arq_pool.unavailable", error=str(exc))
        app.state.arq_pool = None

    try:
        from athenaeum.graph.driver import init_constraints, make_driver

        driver = make_driver(settings.neo4j_url, settings.neo4j_user, settings.neo4j_password)
        await init_constraints(driver)
        app.state.neo4j_driver = driver
        log.info("neo4j.connected")
    except Exception as exc:
        log.warning("neo4j.unavailable", error=str(exc))
        app.state.neo4j_driver = None

    yield

    if app.state.arq_pool is not None:
        await app.state.arq_pool.aclose()
    if getattr(app.state, "neo4j_driver", None) is not None:
        await app.state.neo4j_driver.close()


app = FastAPI(title="Athenaeum API", version=__version__, lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(papers.router)
app.include_router(search.router)
app.include_router(syntheses.router)
app.include_router(bricks.router)
app.include_router(annotations.router)
app.include_router(graph.router)
app.include_router(archive.router)
app.include_router(members.router)
app.include_router(public.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/_debug/settings")
async def debug_settings() -> dict:
    from athenaeum.config import _ENV_FILE
    s = get_settings()
    return {
        "env_file": str(_ENV_FILE),
        "env_file_exists": _ENV_FILE.exists(),
        "orcid_client_id": s.orcid_client_id,
        "orcid_redirect_uri": s.orcid_redirect_uri,
    }
