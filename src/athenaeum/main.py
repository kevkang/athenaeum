from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from athenaeum import __version__
from athenaeum.api import (
    annotations,
    archive,
    audit,
    auth,
    backtrace,
    compose,
    graph,
    highlights,
    history,
    links,
    llm_credentials,
    members,
    openalex_find,
    papers,
    passages,
    projects,
    public,
    search,
    statements,
    syntheses,
    websearch_find,
    zotero,
    zotero_find,
    zotero_highlights,
    zotero_local,
)
from athenaeum.config import DEFAULT_JWT_SECRET, Settings, get_settings

log = structlog.get_logger()


def _reject_insecure_defaults(settings: Settings) -> None:
    """The dev docker-compose.yml (and a bare `pip install -e .` run with no
    .env) both intentionally rely on the hardcoded default JWT secret — fine,
    since it's only ever reachable at localhost. A production deployment
    that forgets to set JWT_SECRET would otherwise boot anyway, silently
    accepting forged session tokens signed with a secret that's public in
    this repo. Fail fast instead of leaking that as a runtime vulnerability.
    """
    if settings.environment != "development" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "Refusing to start: ENVIRONMENT is not 'development' but JWT_SECRET is "
            "still the default value. Set a real JWT_SECRET (see .env.production.example)."
        )


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

    try:
        from playwright.async_api import async_playwright

        # One shared browser process, reused across every .athmd PDF render —
        # launching Chromium fresh per-request costs roughly a second before
        # any actual rendering starts.
        app.state.pw = await async_playwright().start()
        app.state.pw_browser = await app.state.pw.chromium.launch()
        log.info("playwright.connected")
    except Exception as exc:
        log.warning("playwright.unavailable", error=str(exc))
        app.state.pw = None
        app.state.pw_browser = None

    yield

    if app.state.arq_pool is not None:
        await app.state.arq_pool.aclose()
    if getattr(app.state, "neo4j_driver", None) is not None:
        await app.state.neo4j_driver.close()
    if getattr(app.state, "pw_browser", None) is not None:
        await app.state.pw_browser.close()
    if getattr(app.state, "pw", None) is not None:
        await app.state.pw.stop()


_settings = get_settings()
_reject_insecure_defaults(_settings)

app = FastAPI(title="Athenaeum API", version=__version__, lifespan=lifespan)

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
app.include_router(passages.router)
app.include_router(search.router)
app.include_router(syntheses.router)
app.include_router(statements.router)
app.include_router(annotations.router)
app.include_router(compose.router)
app.include_router(highlights.router)
app.include_router(graph.router)
app.include_router(archive.router)
app.include_router(members.router)
app.include_router(public.router)
app.include_router(links.router)
app.include_router(history.router)
app.include_router(zotero.router)
app.include_router(zotero_local.router)
app.include_router(zotero_highlights.router)
app.include_router(zotero_find.router)
app.include_router(openalex_find.router)
app.include_router(websearch_find.router)
app.include_router(llm_credentials.router)
app.include_router(backtrace.router)
app.include_router(audit.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
