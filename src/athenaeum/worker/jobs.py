"""arq job definitions for ingestion, synthesis, and Neo4j graph sync."""
from __future__ import annotations

import asyncio
import uuid
from functools import partial

import httpx
import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from athenaeum.config import get_settings
from athenaeum.crypto import decrypt_secret
from athenaeum.ingest.openalex import fetch_referenced_work_ids
from athenaeum.ingest.pipeline import run_ingest, run_local_zotero_ingest, run_zotero_ingest
from athenaeum.ingest.zotero import ZoteroCreds
from athenaeum.provider.resolver import NoLLMKeyConfigured, get_provider_for_user
from athenaeum.storage.models import (
    BacktraceChain,
    BacktraceHop,
    CitationEdge,
    Paper,
    ZoteroCredentials,
)
from athenaeum.synthesize.backtrace import search_full_text_for_claim
from athenaeum.synthesize.orchestrator import run_synthesis

log = structlog.get_logger()


async def startup(ctx: dict) -> None:
    settings = get_settings()
    ctx["engine"] = create_async_engine(settings.database_url, pool_size=5, max_overflow=10)
    ctx["http"] = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, read=300.0),
        headers={"User-Agent": "athenaeum/0.0.1 (mailto:contact@athenaeum.app)"},
        follow_redirects=True,
    )
    ctx["redis"] = aioredis.from_url(settings.redis_url)

    try:
        from athenaeum.graph.driver import init_constraints, make_driver

        driver = make_driver(settings.neo4j_url, settings.neo4j_user, settings.neo4j_password)
        await init_constraints(driver)
        ctx["neo4j_driver"] = driver
        log.info("worker.neo4j.connected")
    except Exception as exc:
        log.warning("worker.neo4j.unavailable", error=str(exc))
        ctx["neo4j_driver"] = None

    log.info("worker.startup")


async def shutdown(ctx: dict) -> None:
    engine: AsyncEngine = ctx["engine"]
    http: httpx.AsyncClient = ctx["http"]
    r: aioredis.Redis = ctx["redis"]
    await engine.dispose()
    await http.aclose()
    await r.aclose()
    if ctx.get("neo4j_driver") is not None:
        await ctx["neo4j_driver"].close()
    log.info("worker.shutdown")


# ── Ingestion ──────────────────────────────────────────────────────────────────

async def ingest_paper(
    ctx: dict,
    paper_id: str,
    oa_pdf_url: str | None = None,
    s3_key: str | None = None,
    backtrace_hop_id: str | None = None,
) -> None:
    settings = get_settings()
    pid = uuid.UUID(paper_id)
    log.info("job.ingest_paper.start", paper_id=paper_id)

    await run_ingest(
        paper_id=pid,
        oa_pdf_url=oa_pdf_url,
        s3_key=s3_key,
        engine=ctx["engine"],
        http=ctx["http"],
        settings=settings,
    )

    # Populate citation_edges from OpenAlex referenced_works.
    await _sync_citations(ctx, pid, settings)

    if backtrace_hop_id is not None:
        await _resolve_backtrace_hop_after_ingest(ctx, uuid.UUID(backtrace_hop_id), settings)

    log.info("job.ingest_paper.done", paper_id=paper_id)


async def _resolve_backtrace_hop_after_ingest(ctx: dict, hop_id: uuid.UUID, settings) -> None:
    """Tail of ingest_paper when it was triggered by Backtrace escalating from
    an abstract-only search to full text (see api/backtrace.py::_resolve_hop).
    Runs the paper-scoped evidence search now that ingestion has finished (or
    marks the hop as needing a manual PDF/Zotero import if it failed)."""
    engine: AsyncEngine = ctx["engine"]
    async with AsyncSession(engine, expire_on_commit=False) as session:
        hop = await session.get(BacktraceHop, hop_id)
        if hop is None or hop.to_paper_id is None:
            return
        paper = await session.get(Paper, hop.to_paper_id)
        chain = await session.get(BacktraceChain, hop.chain_id)
        if paper is None or chain is None:
            return

        if paper.status != "full_text":
            hop.resolution_status = "awaiting_manual_import"
            await session.commit()
            log.info("backtrace.hop_awaiting_manual_import", hop_id=str(hop_id))
            return

        try:
            provider = await get_provider_for_user(
                session, chain.created_by, settings.zotero_find_model, settings
            )
        except NoLLMKeyConfigured:
            hop.resolution_status = "awaiting_manual_import"
            await session.commit()
            log.warning("backtrace.hop_resolve_no_llm_key", hop_id=str(hop_id))
            return

        passage = await search_full_text_for_claim(
            provider, session, chain.project_id, paper.id, chain.origin_text, settings.embed_model
        )
        if passage is not None:
            hop.quote, hop.relevance_note = passage.verbatim_text, passage.rationale
            hop.quote_source = "full_text"
            hop.resolution_status = "full_text_match"
        else:
            hop.resolution_status = "no_match"
        await session.commit()
        log.info(
            "backtrace.hop_resolved_full_text", hop_id=str(hop_id), status=hop.resolution_status
        )


async def _sync_citations(ctx: dict, paper_id: uuid.UUID, settings) -> None:
    engine: AsyncEngine = ctx["engine"]
    driver = ctx.get("neo4j_driver")

    async with AsyncSession(engine, expire_on_commit=False) as session:
        paper = await session.get(Paper, paper_id)
        if paper is None or not paper.openalex_id:
            return

        # Sync paper node to Neo4j first.
        if driver:
            from athenaeum.graph.sync import upsert_paper
            async with driver.session() as neo:
                await upsert_paper(neo, paper)

        # Fetch referenced OpenAlex IDs in a thread (pyalex is synchronous).
        loop = asyncio.get_event_loop()
        ref_ids: list[str] = await loop.run_in_executor(
            None, partial(fetch_referenced_work_ids, paper.openalex_id)
        )
        if not ref_ids:
            return

        # Find which referenced papers are already in our DB.
        found = list(await session.scalars(
            select(Paper).where(Paper.openalex_id.in_(ref_ids))
        ))
        if not found:
            return

        # Upsert citation_edges (ON CONFLICT DO NOTHING).
        stmt = pg_insert(CitationEdge).values([
            {"from_paper_id": paper.id, "to_paper_id": ref.id, "source": "openalex"}
            for ref in found
        ]).on_conflict_do_nothing()
        await session.execute(stmt)
        await session.commit()

        # Sync CITES edges to Neo4j.
        if driver:
            from athenaeum.graph.sync import sync_paper_cites
            async with driver.session() as neo:
                await sync_paper_cites(neo, str(paper.id), [str(r.id) for r in found])

        log.info(
            "job.citations.synced",
            paper_id=str(paper_id),
            refs_found=len(found),
        )


async def ingest_zotero_item(
    ctx: dict,
    paper_id: str,
    zotero_item_key: str,
    requesting_user_id: str,
) -> None:
    """Ingest a paper acquired from a user's Zotero library.

    Credentials are re-read fresh from the DB and decrypted here — never passed
    through the Redis job payload.
    """
    settings = get_settings()
    pid = uuid.UUID(paper_id)
    engine: AsyncEngine = ctx["engine"]
    log.info("job.ingest_zotero_item.start", paper_id=paper_id, zotero_item_key=zotero_item_key)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        creds_row = await session.get(ZoteroCredentials, uuid.UUID(requesting_user_id))
        if creds_row is None:
            log.warning("job.ingest_zotero_item.no_credentials", paper_id=paper_id)
            paper = await session.get(Paper, pid)
            if paper is not None:
                paper.status = "failed"
                await session.commit()
            return

        creds = ZoteroCreds(
            zotero_user_id=creds_row.zotero_user_id,
            api_key=decrypt_secret(creds_row.api_key_encrypted),
            library_type=creds_row.library_type,
        )

    await run_zotero_ingest(
        paper_id=pid,
        zotero_item_key=zotero_item_key,
        creds=creds,
        engine=engine,
        http=ctx["http"],
        settings=settings,
    )

    # Harmless no-op for most Zotero-sourced papers (they have no openalex_id),
    # but still worth attempting when a Zotero item happens to carry a DOI that
    # OpenAlex can resolve.
    await _sync_citations(ctx, pid, settings)

    log.info("job.ingest_zotero_item.done", paper_id=paper_id)


async def ingest_local_zotero_item(
    ctx: dict,
    paper_id: str,
    zotero_item_key: str,
) -> None:
    """Ingest a paper acquired from a user's local Zotero library — no
    credentials involved, just the operator's chosen subfolder of the
    bind-mounted home directory (see zotero_local_config / "Select local
    library")."""
    settings = get_settings()
    pid = uuid.UUID(paper_id)
    engine: AsyncEngine = ctx["engine"]
    log.info(
        "job.ingest_local_zotero_item.start", paper_id=paper_id, zotero_item_key=zotero_item_key
    )

    await run_local_zotero_ingest(
        paper_id=pid,
        zotero_item_key=zotero_item_key,
        engine=engine,
        http=ctx["http"],
        settings=settings,
    )

    await _sync_citations(ctx, pid, settings)

    log.info("job.ingest_local_zotero_item.done", paper_id=paper_id)


# ── Synthesis ──────────────────────────────────────────────────────────────────

async def run_synthesis_job(
    ctx: dict,
    synthesis_id: str,
    requesting_user_id: str,
) -> None:
    settings = get_settings()
    log.info("job.synthesis.start", synthesis_id=synthesis_id)
    await run_synthesis(
        synthesis_id=uuid.UUID(synthesis_id),
        engine=ctx["engine"],
        redis=ctx["redis"],
        settings=settings,
        requesting_user_id=uuid.UUID(requesting_user_id),
    )
    log.info("job.synthesis.done", synthesis_id=synthesis_id)


# ── Graph sync ─────────────────────────────────────────────────────────────────

async def sync_project_graph(ctx: dict, project_id: str) -> None:
    driver = ctx.get("neo4j_driver")
    if driver is None:
        log.warning("graph_sync.neo4j_unavailable", project_id=project_id)
        return

    from athenaeum.graph.sync import sync_project_graph as _sync

    await _sync(driver, ctx["engine"], uuid.UUID(project_id))
