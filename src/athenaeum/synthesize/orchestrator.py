"""Synthesis orchestrator — runs the five-step pipeline as an arq job."""
from __future__ import annotations

import json
import uuid
from datetime import UTC
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.config import Settings
from athenaeum.domain.models import SentenceHit
from athenaeum.ingest.embed import embed_sentences
from athenaeum.provider.base import Message, Provider
from athenaeum.provider.resolver import get_provider_for_user
from athenaeum.retrieve.hybrid import search as hybrid_search
from athenaeum.storage.models import (
    PaperSentence,
    Passage,
    Synthesis,
    SynthesisEvidence,
    SynthesisStep,
)
from athenaeum.synthesize.passage_util import is_contiguous
from athenaeum.synthesize.prompts import (
    CLASSIFY_SIGNALS_TOOL,
    EXTRACT_PASSAGES_TOOL,
    EXTRACT_SYSTEM,
    INTERPRET_QUERY_TOOL,
    QUERY_INTERP_SYSTEM,
    SIGNAL_SYSTEM,
    VERIFY_PASSAGES_TOOL,
    VERIFY_SYSTEM,
)
from athenaeum.synthesize.verifier import verify_passages

log = structlog.get_logger()


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _publish(
    session: AsyncSession,
    redis: aioredis.Redis,
    synthesis_id: uuid.UUID,
    ord: int,
    kind: str,
    payload: dict[str, Any],
) -> None:
    step = SynthesisStep(
        id=uuid.uuid4(),
        synthesis_id=synthesis_id,
        ord=ord,
        kind=kind,
        payload_json=payload,
    )
    session.add(step)
    await session.flush()
    await redis.publish(
        f"synthesis:{synthesis_id}:steps",
        json.dumps({"kind": kind, "ord": ord, "payload": payload}),
    )


# ── Step functions ─────────────────────────────────────────────────────────────

async def _step_query_interp(
    provider: Provider,
    claim: str,
) -> dict[str, Any]:
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=f"Parse this research claim:\n\n{claim}")],
        system=QUERY_INTERP_SYSTEM,
        tools=[INTERPRET_QUERY_TOOL],
    )
    if tool_result is None:
        raise RuntimeError("query_interp: no tool result from provider")
    return tool_result["input"]


async def _step_retrieval(
    session: AsyncSession,
    project_id: uuid.UUID,
    search_queries: list[str],
    embed_model: str,
    top_k_per_query: int = 10,
) -> list[SentenceHit]:
    seen: set[uuid.UUID] = set()
    all_hits: list[SentenceHit] = []
    for query in search_queries:
        embedding = (await embed_sentences([query], embed_model))[0]
        hits = await hybrid_search(
            session=session,
            project_id=project_id,
            query_text=query,
            query_embedding=embedding,
            top_k=top_k_per_query,
            mode="hybrid",
        )
        for hit in hits:
            if hit.sentence_id not in seen:
                seen.add(hit.sentence_id)
                all_hits.append(hit)
    return all_hits


def _format_hits_for_prompt(hits: list[SentenceHit]) -> str:
    lines: list[str] = []
    for h in hits:
        lines.append(
            f"[{h.sentence_id}] ({h.paper.title[:60]}, {h.paper.year or '?'}, "
            f"§{h.section_label}) {h.text}"
        )
    return "\n".join(lines)


async def _step_extract(
    provider: Provider,
    claim: str,
    hits: list[SentenceHit],
    session: AsyncSession,
) -> list[Passage]:
    hits_text = _format_hits_for_prompt(hits)
    user_msg = (
        f"CLAIM: {claim}\n\n"
        f"RETRIEVED SENTENCES ({len(hits)} total):\n{hits_text}\n\n"
        "Select sentences that provide evidence for the claim."
    )
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=user_msg)],
        system=EXTRACT_SYSTEM,
        tools=[EXTRACT_PASSAGES_TOOL],
    )
    if tool_result is None:
        raise RuntimeError("extract: no tool result from provider")

    # Validate sentence IDs and build Passage objects
    valid_ids = {str(h.sentence_id): h for h in hits}
    passages: list[Passage] = []

    def _clean_uuid(raw: str) -> str:
        """Strip surrounding whitespace/quotes that LLMs sometimes add."""
        import re
        return re.sub(r"[^0-9a-fA-F\-]", "", raw.strip())

    for pd in tool_result["input"].get("passages", []):
        raw_sids = [_clean_uuid(str(s)) for s in pd.get("sentence_ids", [])]
        # Filter to only IDs that were actually retrieved
        valid_sids = [sid for sid in raw_sids if sid in valid_ids]
        if not valid_sids:
            log.warning("extract.invalid_sentence_ids", returned=raw_sids)
            continue

        try:
            sentence_uuids = [uuid.UUID(sid) for sid in valid_sids]
        except ValueError as exc:
            log.warning("extract.bad_sentence_uuid", error=str(exc))
            continue

        # Fetch ground-truth text from DB in sentence order
        rows = (
            await session.scalars(
                select(PaperSentence)
                .where(PaperSentence.id.in_(sentence_uuids))
                .order_by(PaperSentence.ord)
            )
        ).all()
        if not is_contiguous([r.ord for r in rows]):
            # A non-contiguous selection would still join into what reads as
            # one continuous quote, silently skipping whatever sentence(s)
            # sit in the gap. Reject rather than publish a spliced "verbatim"
            # passage — mirrors the invalid_sentence_ids/bad_sentence_uuid
            # handling above.
            log.warning(
                "extract.non_contiguous_sentences",
                sentence_ids=valid_sids,
                ords=[r.ord for r in rows],
            )
            continue
        verbatim = " ".join(r.text for r in rows)

        # paper_id: prefer what the LLM returned, fall back to the hit's paper id
        raw_paper_id = pd.get("paper_id")
        fallback_paper_id = str(valid_ids[valid_sids[0]].paper.id)
        try:
            paper_id = (
                uuid.UUID(_clean_uuid(str(raw_paper_id))) if raw_paper_id
                else uuid.UUID(fallback_paper_id)
            )
        except ValueError:
            paper_id = uuid.UUID(fallback_paper_id)

        passage = Passage(
            id=uuid.uuid4(),
            paper_id=paper_id,
            sentence_ids=sentence_uuids,
            verbatim_text=verbatim,
            rationale=pd.get("rationale") or None,
        )
        session.add(passage)
        passages.append(passage)

    await session.flush()
    return passages


async def _step_signal(
    provider: Provider,
    claim: str,
    passages: list[Passage],
) -> list[dict[str, Any]]:
    passage_lines = "\n".join(
        f"[{i}] {p.verbatim_text[:300]}…" if len(p.verbatim_text) > 300
        else f"[{i}] {p.verbatim_text}"
        for i, p in enumerate(passages)
    )
    user_msg = f"CLAIM: {claim}\n\nPASSAGES:\n{passage_lines}"

    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=user_msg)],
        system=SIGNAL_SYSTEM,
        tools=[CLASSIFY_SIGNALS_TOOL],
    )
    if tool_result is None:
        raise RuntimeError("signal: no tool result from provider")
    return tool_result["input"].get("classifications", [])


# ── Main entry point ───────────────────────────────────────────────────────────

async def run_synthesis(
    synthesis_id: uuid.UUID,
    engine: AsyncEngine,
    redis: aioredis.Redis,
    settings: Settings,
    requesting_user_id: uuid.UUID,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        synthesis = await session.get(Synthesis, synthesis_id)
        if synthesis is None:
            log.error("synthesis.not_found", synthesis_id=str(synthesis_id))
            return

        step_ord = 0
        try:
            # Resolved here (inside the try) rather than before the session
            # opens, so a user disconnecting their key between job enqueue
            # and pickup (NoLLMKeyConfigured) fails the synthesis gracefully
            # via the except block below instead of crashing the worker.
            provider = await get_provider_for_user(
                session, requesting_user_id, settings.synthesis_model, settings
            )
            checker = await get_provider_for_user(
                session, requesting_user_id, settings.checker_model, settings
            )

            log.info("synthesis.start", synthesis_id=str(synthesis_id))

            # ── 1. Query interpretation ────────────────────────────────────────
            expansion = await _step_query_interp(provider, synthesis.claim_text)
            synthesis.query_expansion_json = expansion
            await _publish(session, redis, synthesis_id, step_ord, "query_interp", expansion)
            step_ord += 1

            # ── 2. Retrieval ───────────────────────────────────────────────────
            hits = await _step_retrieval(
                session=session,
                project_id=synthesis.project_id,
                search_queries=expansion["search_queries"],
                embed_model=settings.embed_model,
            )
            retrieval_payload = {
                "queries": expansion["search_queries"],
                "n_hits": len(hits),
            }
            await _publish(session, redis, synthesis_id, step_ord, "retrieval", retrieval_payload)
            step_ord += 1

            if not hits:
                synthesis.status = "failed"
                await session.commit()
                await redis.publish(
                    f"synthesis:{synthesis_id}:steps",
                    json.dumps({"kind": "failed", "reason": "no hits"}),
                )
                return

            # ── 3. Sentence extraction ─────────────────────────────────────────
            passages = await _step_extract(provider, synthesis.claim_text, hits, session)
            await _publish(
                session, redis, synthesis_id, step_ord, "extract",
                {"n_passages": len(passages)},
            )
            step_ord += 1

            if not passages:
                synthesis.status = "failed"
                await session.commit()
                await redis.publish(
                    f"synthesis:{synthesis_id}:steps",
                    json.dumps({"kind": "failed", "reason": "no passages extracted"}),
                )
                return

            # ── 4. Signal detection ────────────────────────────────────────────
            classifications = await _step_signal(provider, synthesis.claim_text, passages)
            for ord_idx, cls in enumerate(classifications):
                idx = cls.get("passage_index", ord_idx)
                if idx >= len(passages):
                    continue
                evidence = SynthesisEvidence(
                    synthesis_id=synthesis_id,
                    passage_id=passages[idx].id,
                    role=cls.get("role", "data"),
                    ord=ord_idx,
                )
                session.add(evidence)
            await session.flush()

            signal_payload = {
                "roles": [c.get("role") for c in classifications],
                "directions": [c.get("direction") for c in classifications],
            }
            await _publish(session, redis, synthesis_id, step_ord, "signal", signal_payload)
            step_ord += 1

            # ── 5. Independent verification ────────────────────────────────────
            # checker has NO access to provider, expansion, hits, or classifications
            verification_rate, verified_flags = await verify_passages(
                checker=checker,
                claim=synthesis.claim_text,
                passages=passages,
                checker_model_name=settings.checker_model,
                system_prompt=VERIFY_SYSTEM,
                tool=VERIFY_PASSAGES_TOOL,
            )

            synthesis.verification_rate = verification_rate
            synthesis.checker_model = settings.checker_model

            # Mark passages as verified / not verified. strict=True: verify_passages()
            # always returns one flag per passage by construction — a mismatch would
            # mean flags got silently misassigned to the wrong passages.
            for passage, verified in zip(passages, verified_flags, strict=True):
                passage.verified = verified
                if verified:
                    from datetime import datetime
                    passage.verified_at = datetime.now(UTC)
                    passage.checker_model = settings.checker_model

            synthesis.status = "done"
            await session.commit()

            verify_payload = {
                "verification_rate": verification_rate,
                "verified": sum(verified_flags),
                "total": len(verified_flags),
            }
            await _publish(session, redis, synthesis_id, step_ord, "verify", verify_payload)

            await redis.publish(
                f"synthesis:{synthesis_id}:steps",
                json.dumps({"kind": "done", "verification_rate": verification_rate}),
            )
            log.info(
                "synthesis.done",
                synthesis_id=str(synthesis_id),
                verification_rate=verification_rate,
                n_passages=len(passages),
            )

        except Exception:
            log.exception("synthesis.failed", synthesis_id=str(synthesis_id))
            await session.rollback()
            synthesis = await session.get(Synthesis, synthesis_id)
            if synthesis is not None:
                synthesis.status = "failed"
                await session.commit()
            await redis.publish(
                f"synthesis:{synthesis_id}:steps",
                json.dumps({"kind": "failed"}),
            )
            raise
