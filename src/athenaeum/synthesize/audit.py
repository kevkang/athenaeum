"""Audit mode — for a compose document, does each piece of cited evidence
actually support the statement it's attached to? Runs as an arq job,
mirroring synthesize/orchestrator.py's shape almost exactly: the same
independent-checker pattern verify_passages() already uses for Synthesis,
just asking a document-authoring question ("does this support the claim?")
instead of a hallucination-detection one ("is this real, verbatim text?").

Unlike Synthesis there's no separate step-log table — each AuditFinding row
IS the unit of progress, persisted and published over Redis at the same
time (see storage/models.py's AuditRun/AuditFinding docstrings). Statements
with no evidence at all aren't given a finding row (there's nothing to
check) — the API layer surfaces those separately, computed fresh from
domain/citations.py rather than persisted, same as citation numbers
everywhere else in this app.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from athenaeum.config import Settings
from athenaeum.domain.citations import (
    EvidenceEntry,
    build_reference_numbering,
    iter_statement_ids_in_order,
)
from athenaeum.provider.base import Message, Provider
from athenaeum.provider.resolver import get_provider_for_user
from athenaeum.storage.models import AuditFinding, AuditRun, ComposeDocument, Statement
from athenaeum.synthesize.prompts import AUDIT_SYSTEM, AUDIT_TOOL

log = structlog.get_logger()


async def audit_evidence(
    checker: Provider,
    assertion: str,
    quotes: list[str],
    checker_model_name: str,
) -> list[tuple[bool, str]]:
    """Run the independent audit pass for one statement's quoted evidence.
    Mirrors verify_passages()'s shape (synthesize/verifier.py) but returns a
    (supported, rationale) pair per item — the rationale is the actual
    deliverable of an audit, not an internal signal."""
    if not quotes:
        return []

    quote_lines = "\n\n".join(f"[{i}] {q}" for i, q in enumerate(quotes))
    user_msg = (
        f"CLAIM:\n{assertion}\n\n"
        f"CITED EVIDENCE ({len(quotes)} total):\n{quote_lines}\n\n"
        "For each piece of evidence, judge whether it actually supports the claim."
    )
    _, tool_result = await checker.complete(
        messages=[Message(role="user", content=user_msg)],
        system=AUDIT_SYSTEM,
        max_tokens=2048,
        tools=[AUDIT_TOOL],
    )
    if tool_result is None:
        log.warning("audit.no_tool_result", n_quotes=len(quotes), checker_model=checker_model_name)
        return [(False, "Audit check failed to return a result.")] * len(quotes)

    results: list[dict[str, Any]] = tool_result["input"].get("results", [])
    verdicts: dict[int, tuple[bool, str]] = {}
    for r in results:
        idx = r.get("evidence_index", -1)
        if 0 <= idx < len(quotes):
            verdicts[idx] = (bool(r.get("supported")), str(r.get("rationale") or ""))

    return [
        verdicts.get(i, (False, "No verdict returned for this item."))
        for i in range(len(quotes))
    ]


_NO_QUOTE_RATIONALE = (
    "This citation has no specific verbatim evidence attached — too general to verify "
    "that it supports the claim."
)


async def run_audit(
    audit_run_id: uuid.UUID,
    engine: AsyncEngine,
    redis: aioredis.Redis,
    settings: Settings,
    requesting_user_id: uuid.UUID,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        audit_run = await session.get(AuditRun, audit_run_id)
        if audit_run is None:
            log.error("audit.not_found", audit_run_id=str(audit_run_id))
            return

        channel = f"audit:{audit_run_id}:steps"
        ord_counter = 0
        try:
            # Resolved inside the try, same reasoning as run_synthesis: a
            # user disconnecting their key between enqueue and pickup fails
            # the run gracefully via the except block, not the worker.
            checker = await get_provider_for_user(
                session, requesting_user_id, settings.checker_model, settings
            )
            log.info("audit.start", audit_run_id=str(audit_run_id))

            doc = await session.get(ComposeDocument, audit_run.compose_document_id)
            if doc is None:
                raise RuntimeError(f"Compose document {audit_run.compose_document_id} not found")

            statement_ids = iter_statement_ids_in_order(doc.content_json)
            numbering = await build_reference_numbering(session, statement_ids)

            evidence_by_sub_id: dict[str, EvidenceEntry] = {}
            for ref in numbering.references:
                for e in ref.evidence:
                    evidence_by_sub_id[e.sub_id] = e

            statements: dict[uuid.UUID, Statement] = {}
            if statement_ids:
                rows = await session.scalars(
                    select(Statement).where(Statement.id.in_(statement_ids))
                )
                statements = {s.id: s for s in rows}

            for statement_id in statement_ids:
                statement = statements.get(statement_id)
                sub_ids = numbering.statement_markers.get(statement_id, [])
                if statement is None or not sub_ids:
                    continue  # no evidence to audit — surfaced separately, no LLM call

                quoted_sub_ids: list[str] = []
                quoted_quotes: list[str] = []
                unquoted_sub_ids: list[str] = []
                for sub_id in sub_ids:
                    entry = evidence_by_sub_id[sub_id]
                    if entry.quote is not None:
                        quoted_sub_ids.append(sub_id)
                        quoted_quotes.append(entry.quote)
                    else:
                        unquoted_sub_ids.append(sub_id)

                verdicts: dict[str, tuple[bool, str]] = {}
                if quoted_quotes:
                    results = await audit_evidence(
                        checker=checker,
                        assertion=statement.assertion,
                        quotes=quoted_quotes,
                        checker_model_name=settings.checker_model,
                    )
                    for sub_id, verdict in zip(quoted_sub_ids, results, strict=True):
                        verdicts[sub_id] = verdict
                for sub_id in unquoted_sub_ids:
                    verdicts[sub_id] = (False, _NO_QUOTE_RATIONALE)

                for sub_id in sub_ids:
                    entry = evidence_by_sub_id[sub_id]
                    supported, rationale = verdicts[sub_id]
                    finding = AuditFinding(
                        id=uuid.uuid4(),
                        audit_run_id=audit_run_id,
                        ord=ord_counter,
                        statement_id=statement_id,
                        sub_id=sub_id,
                        target_kind=entry.target_kind,
                        target_id=entry.target_id,
                        quote=entry.quote,
                        supported=supported,
                        rationale=rationale,
                    )
                    session.add(finding)
                    await session.flush()
                    await redis.publish(channel, json.dumps({
                        "kind": "finding",
                        "ord": ord_counter,
                        "payload": {
                            "statement_id": str(statement_id),
                            "sub_id": sub_id,
                            "target_kind": entry.target_kind,
                            "target_id": str(entry.target_id),
                            "quote": entry.quote,
                            "supported": supported,
                            "rationale": rationale,
                        },
                    }))
                    ord_counter += 1
                await session.commit()

            audit_run.status = "done"
            audit_run.model = settings.checker_model
            await session.commit()
            await redis.publish(channel, json.dumps({"kind": "done"}))
            log.info("audit.done", audit_run_id=str(audit_run_id), n_findings=ord_counter)

        except Exception:
            log.exception("audit.failed", audit_run_id=str(audit_run_id))
            await session.rollback()
            audit_run = await session.get(AuditRun, audit_run_id)
            if audit_run is not None:
                audit_run.status = "failed"
                await session.commit()
            await redis.publish(channel, json.dumps({"kind": "failed"}))
            raise
