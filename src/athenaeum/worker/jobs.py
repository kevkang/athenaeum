"""arq job definitions for ingestion, synthesis, and Neo4j graph sync."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from functools import partial

import httpx
import redis.asyncio as aioredis
import structlog
from arq import create_pool
from arq.connections import RedisSettings
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
from athenaeum.synthesize.audit import run_audit
from athenaeum.synthesize.backtrace import search_full_text_for_claim
from athenaeum.synthesize.draft_audit import run_draft_audit
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
    # A job that needs to enqueue another (Nucleate's background PDF fetch)
    # can't use ctx["redis"] above: it replaces arq's own connection with a
    # plain client that has no enqueue_job.
    ctx["arq_pool"] = await create_pool(RedisSettings.from_dsn(settings.redis_url))

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
    if ctx.get("arq_pool") is not None:
        await ctx["arq_pool"].aclose()
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

    # If this paper is a node in any Nucleate corpus, bring those nodes up to
    # full-text evidence (D-N15). Guarded: a failed upgrade must never undo a
    # successful ingestion, and it can be re-run.
    try:
        await _upgrade_corpus_nodes(ctx, pid, settings)
    except Exception:
        log.exception("job.ingest_paper.corpus_upgrade_failed", paper_id=paper_id)

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

        # What this hop is meant to support is its parent's quote (or the
        # chain's origin text at depth 0) — not always the origin text
        # regardless of depth, which this silently did before backtrace
        # became a tree and hops beyond depth 0 became common.
        citing_text = chain.origin_text
        if hop.parent_hop_id is not None:
            parent_hop = await session.get(BacktraceHop, hop.parent_hop_id)
            if parent_hop is not None and parent_hop.quote:
                citing_text = parent_hop.quote

        passage = await search_full_text_for_claim(
            provider, session, chain.project_id, paper.id, citing_text, settings.embed_model
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


# ── Audit mode ─────────────────────────────────────────────────────────────────

async def run_audit_job(
    ctx: dict,
    audit_run_id: str,
    requesting_user_id: str,
) -> None:
    settings = get_settings()
    log.info("job.audit.start", audit_run_id=audit_run_id)
    await run_audit(
        audit_run_id=uuid.UUID(audit_run_id),
        engine=ctx["engine"],
        redis=ctx["redis"],
        settings=settings,
        requesting_user_id=uuid.UUID(requesting_user_id),
    )
    log.info("job.audit.done", audit_run_id=audit_run_id)


async def run_draft_audit_job(
    ctx: dict,
    audit_run_id: str,
    requesting_user_id: str,
) -> None:
    """The Audit tab's pasted-draft run. Separate from run_audit_job so the two
    triggers stay distinguishable in the job log as well as in the run record."""
    settings = get_settings()
    log.info("job.draft_audit.start", audit_run_id=audit_run_id)
    await run_draft_audit(
        audit_run_id=uuid.UUID(audit_run_id),
        engine=ctx["engine"],
        redis=ctx["redis"],
        settings=settings,
        requesting_user_id=uuid.UUID(requesting_user_id),
    )
    log.info("job.draft_audit.done", audit_run_id=audit_run_id)


# ── Graph sync ─────────────────────────────────────────────────────────────────

async def sync_project_graph(ctx: dict, project_id: str) -> None:
    driver = ctx.get("neo4j_driver")
    if driver is None:
        log.warning("graph_sync.neo4j_unavailable", project_id=project_id)
        return

    from athenaeum.graph.sync import sync_project_graph as _sync

    await _sync(driver, ctx["engine"], uuid.UUID(project_id))


async def run_expansion_job(
    ctx: dict,
    expansion_id: str,
    requesting_user_id: str,
) -> None:
    """One Nucleate expansion (nucleate/archivist.py), wired to real sources,
    the user's model credentials, and the Redis event channel."""
    from athenaeum.api.nucleate import event_channel
    from athenaeum.ingest.acquire import unpaywall_pdf_url
    from athenaeum.nucleate.archivist import ArchivistDeps, run_expansion
    from athenaeum.nucleate.retrieval import retrieve_library, retrieve_openalex, retrieve_web
    from athenaeum.storage.models import NucleusExpansion, NucleusExpansionEvent

    settings = get_settings()
    redis = ctx["redis"]
    channel = event_channel(expansion_id)
    expansion_uuid = uuid.UUID(expansion_id)
    user_uuid = uuid.UUID(requesting_user_id)
    log.info("job.expansion.start", expansion_id=expansion_id)

    async with AsyncSession(ctx["engine"], expire_on_commit=False) as session:
        try:
            archivist = await get_provider_for_user(
                session, user_uuid, settings.archivist_model, settings)
            checker = await get_provider_for_user(
                session, user_uuid, settings.checker_model, settings)
        except Exception as exc:
            # No usable model key: fail the run visibly rather than leaving it
            # "running" forever with no event explaining why.
            expansion = await session.get(NucleusExpansion, expansion_uuid)
            if expansion is not None:
                expansion.status = "failed"
                expansion.finished_at = datetime.now(UTC)
                session.add(NucleusExpansionEvent(
                    id=uuid.uuid4(), expansion_id=expansion_uuid, ord=0,
                    stage="interpreting", status="failed", message=str(exc)[:300]))
                await session.commit()
            await redis.publish(channel, json.dumps(
                {"ord": 0, "stage": "interpreting", "status": "failed", "node_id": None,
                 "message": str(exc)[:300]}))
            log.warning("job.expansion.no_provider", expansion_id=expansion_id)
            return

        async def library(s: AsyncSession, project_id: uuid.UUID, query: str):
            return await retrieve_library(s, project_id, query, settings.embed_model)

        async def openalex(queries: list[str]):
            return await retrieve_openalex(queries, email=settings.unpaywall_email)

        async def web(query: str):
            return await retrieve_web(archivist, query, email=settings.unpaywall_email)

        async def publish(event: dict) -> None:
            await redis.publish(channel, json.dumps(event))

        async def find_open_access(doi: str) -> str | None:
            return await unpaywall_pdf_url(doi, settings.unpaywall_email, ctx["http"])

        async def request_fulltext(paper_id: uuid.UUID, url: str) -> None:
            # Keyed by paper, so two expansions wanting the same PDF don't
            # fetch and parse it twice.
            await ctx["arq_pool"].enqueue_job(
                "ingest_paper", str(paper_id), oa_pdf_url=url, _job_id=f"ingest_paper:{paper_id}"
            )

        deps = ArchivistDeps(
            archivist=archivist, checker=checker, checker_model=settings.checker_model,
            embed_model=settings.embed_model, library=library, openalex=openalex, web=web,
            publish=publish, find_open_access=find_open_access,
            request_fulltext=request_fulltext,
        )
        await run_expansion(session, expansion_uuid, deps, user_uuid)
    log.info("job.expansion.done", expansion_id=expansion_id)


async def _upgrade_corpus_nodes(ctx: dict, paper_id: uuid.UUID, settings) -> None:
    """Run nucleate/fulltext.upgrade_nodes_for_paper, with model access
    resolved per nucleus: its creator's credentials, else the project owner's."""
    from athenaeum.nucleate.fulltext import UpgradeDeps, upgrade_nodes_for_paper
    from athenaeum.storage.models import CorpusNode, Nucleus, Project

    async with AsyncSession(ctx["engine"], expire_on_commit=False) as session:
        in_corpus = await session.scalar(
            select(CorpusNode.id).where(CorpusNode.paper_id == paper_id).limit(1)
        )
        if in_corpus is None:
            return
        cache: dict[uuid.UUID, UpgradeDeps] = {}

        async def deps_for(nucleus: Nucleus) -> UpgradeDeps:
            user_id = nucleus.created_by
            if user_id is None:
                project = await session.get(Project, nucleus.project_id)
                assert project is not None
                user_id = project.owner_id
            if user_id not in cache:
                cache[user_id] = UpgradeDeps(
                    provider=await get_provider_for_user(
                        session, user_id, settings.archivist_model, settings),
                    checker=await get_provider_for_user(
                        session, user_id, settings.checker_model, settings),
                    checker_model=settings.checker_model, embed_model=settings.embed_model,
                    user_id=user_id,
                )
            return cache[user_id]

        await upgrade_nodes_for_paper(session, paper_id, deps_for)
