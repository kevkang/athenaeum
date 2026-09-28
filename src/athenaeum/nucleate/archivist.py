"""One expansion: from a prompt to reviewed-ready nodes on the canvas.

Stages, in the handoff's order (Part V). The first three run once over the
whole expansion; the rest run per paper, so each node becomes readable as soon
as it is done rather than all at the end:

  interpreting        prompt + anchors -> searches (or a polite decline if the
                      prompt asks the archivist to synthesize)
  retrieving          library, OpenAlex, web
  ranking             drop what's already in this nucleus, choose <= budget
  -- per paper --
  (placed)            the node appears, "arriving", status fetching
  ingesting           settle what text is in hand (library full text, abstract)
  extracting          find verbatim evidence in it
  interpreting_papers write the relevance statement from that evidence
  verifying           independent audit of the statement against the evidence
  placing             commit it; the node becomes ready
  --
  awaiting_review     explicit: the archivist is done, the next move is yours

Every stage transition and per-node status change is an event: persisted to
nucleus_expansion_events (with the tokens spent since the previous event, so
cost can be attributed by stage) and published for the live stream. Counts are
real — "3 of 7", never an estimate.

Background full-text fetching (D-N15) is not done here: this stage only settles
what text is already in hand. Fetching open-access PDFs and upgrading a node's
evidence when one lands is a separate job.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain import nucleate as v
from athenaeum.domain.source_check import text_quote_selector_in
from athenaeum.nucleate.corpus import RejectedUnderThisNucleus, place_node
from athenaeum.nucleate.retrieval import Candidate, WebResult, merge_candidates
from athenaeum.provider.base import Message, Provider
from athenaeum.storage.models import (
    AuditFinding,
    AuditRun,
    CorpusNode,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusExpansionEvent,
    NucleusPrompt,
    Paper,
)
from athenaeum.synthesize.audit import audit_evidence
from athenaeum.synthesize.backtrace import search_full_text_for_claim
from athenaeum.synthesize.find_ranking import FindCandidate, rank_candidates
from athenaeum.synthesize.prompts import (
    ARCHIVIST_INTERPRET_SYSTEM,
    ARCHIVIST_INTERPRET_TOOL,
    ARCHIVIST_RANK_SYSTEM,
    ARCHIVIST_RANK_TOOL,
    ARCHIVIST_RELEVANCE_SYSTEM,
    ARCHIVIST_RELEVANCE_TOOL,
)

log = structlog.get_logger()

PER_PAPER_STAGES: tuple[str, ...] = (
    "ingesting", "extracting", "interpreting_papers", "verifying", "placing",
)

# How many candidates the ranking stage is shown, and how much of each
# abstract. Enough to judge relevance, bounded so one expansion's prompt stays
# a predictable size and cost.
MAX_RANKED_CANDIDATES = 40
ABSTRACT_CHARS_FOR_RANKING = 600


# An Unpaywall answer is reused for this long before asking again: open-access
# status changes slowly, and repeat expansions shouldn't re-query every paper.
OA_RECHECK_AFTER = timedelta(days=7)


async def request_background_fulltext(deps: ArchivistDeps, paper: Paper) -> bool:
    """Look for a legal open-access copy of `paper` and, if there is one, ask
    for it to be fetched and parsed in the background (D-N15).

    The answer is recorded on the paper either way, because "checked, no
    open-access copy" is exactly what the needs-full-text queue must show.
    Returns whether a fetch was requested.
    """
    if deps.find_open_access is None or deps.request_fulltext is None or not paper.doi:
        return False
    now = datetime.now(UTC)
    if paper.oa_checked_at is None or now - paper.oa_checked_at >= OA_RECHECK_AFTER:
        paper.oa_pdf_url = await deps.find_open_access(paper.doi)
        paper.oa_checked_at = now
    if not paper.oa_pdf_url:
        return False
    await deps.request_fulltext(paper.id, paper.oa_pdf_url)
    return True


class ExpansionCancelled(Exception):
    """The user cancelled. Raised at a stage or paper boundary; nodes already
    completed are kept."""


@dataclass
class ArchivistDeps:
    """Everything the run talks to outside the database, injectable so the
    whole pipeline can be tested without a model or a network."""

    archivist: Provider
    checker: Provider
    checker_model: str
    embed_model: str
    library: Callable[[AsyncSession, uuid.UUID, str], Awaitable[list[Candidate]]]
    openalex: Callable[[list[str]], Awaitable[list[Candidate]]]
    web: Callable[[str], Awaitable[WebResult]]
    publish: Callable[[dict[str, Any]], Awaitable[None]]
    # D-N15, background full text. `find_open_access` looks a DOI up on
    # Unpaywall; `request_fulltext` asks for the PDF to be fetched and parsed
    # later. Either left None and no fetch is attempted — nodes keep whatever
    # text was already in hand.
    find_open_access: Callable[[str], Awaitable[str | None]] | None = None
    request_fulltext: Callable[[uuid.UUID, str], Awaitable[None]] | None = None


# ── Events ───────────────────────────────────────────────────────────────────


class Emitter:
    """Persists and publishes events, and meters tokens between them."""

    def __init__(
        self, session: AsyncSession, expansion: NucleusExpansion, deps: ArchivistDeps
    ) -> None:
        self._session = session
        self._expansion = expansion
        self._deps = deps
        self._ord = 0
        self._last_in, self._last_out = self._totals()

    def _totals(self) -> tuple[int, int]:
        providers = (self._deps.archivist, self._deps.checker)
        # A provider shared by both roles must not be counted twice.
        unique = {id(p): p for p in providers}.values()
        return (
            sum(getattr(p, "input_tokens", 0) for p in unique),
            sum(getattr(p, "output_tokens", 0) for p in unique),
        )

    async def emit(
        self,
        stage: str,
        status: str,
        *,
        node_id: uuid.UUID | None = None,
        counts: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        total_in, total_out = self._totals()
        spent_in, spent_out = total_in - self._last_in, total_out - self._last_out
        self._last_in, self._last_out = total_in, total_out

        self._expansion.stage = stage
        self._expansion.input_tokens += spent_in
        self._expansion.output_tokens += spent_out
        row = NucleusExpansionEvent(
            id=uuid.uuid4(), expansion_id=self._expansion.id, ord=self._ord, stage=stage,
            status=status, node_id=node_id, counts=counts, message=message,
            input_tokens=spent_in, output_tokens=spent_out,
        )
        self._session.add(row)
        # Committed per event so anyone polling sees progress as it happens.
        await self._session.commit()
        await self._deps.publish({
            "ord": self._ord, "stage": stage, "status": status,
            "node_id": str(node_id) if node_id else None, "counts": counts,
            "message": message, "at": datetime.now(UTC).isoformat(),
        })
        self._ord += 1


# ── Model steps ──────────────────────────────────────────────────────────────


@dataclass
class RetrievalPlan:
    asks_for_synthesis: bool
    decline_reply: str | None
    library_query: str
    scholarly_queries: list[str]
    web_query: str


def _paper_label(paper: Paper) -> str:
    from athenaeum.domain.authors import first_author_name

    author = first_author_name(paper.authors_json) or "Unknown"
    return f"{author} {paper.year or 'n.d.'}: {paper.title}"


async def interpret(
    provider: Provider,
    question: str,
    instruction: str,
    anchors: Sequence[tuple[Paper, NodeReview]],
    pinned: Sequence[Paper],
    rejected: Sequence[tuple[Paper, NodeReview]],
) -> RetrievalPlan:
    """Turn the instruction into searches, or detect a request to synthesize."""
    lines = [f"NUCLEUS QUESTION: {question}", f"INSTRUCTION: {instruction}"]
    if anchors:
        lines.append("ANCHORS (the researcher selected these to expand from):")
        lines += [f"  [{i}] {_paper_label(p)} — {r.relevance_text or ''}"
                  for i, (p, r) in enumerate(anchors)]
    if pinned:
        lines.append("PINNED:")
        lines += [f"  - {_paper_label(p)}" for p in pinned]
    if rejected:
        lines.append("REJECTED (steer away from papers like these):")
        lines += [f"  - {_paper_label(p)} — reason: {r.rejection_reason or 'none given'}"
                  for p, r in rejected]

    _, tool_result = await provider.complete(
        messages=[Message(role="user", content="\n".join(lines))],
        system=ARCHIVIST_INTERPRET_SYSTEM,
        max_tokens=1024,
        tools=[ARCHIVIST_INTERPRET_TOOL],
    )
    if tool_result is None:
        raise RuntimeError("interpreting: the model returned no plan")
    data = tool_result["input"]
    queries = [q for q in (data.get("scholarly_queries") or []) if isinstance(q, str)]
    return RetrievalPlan(
        asks_for_synthesis=bool(data.get("asks_for_synthesis")),
        decline_reply=data.get("decline_reply"),
        library_query=str(data.get("library_query") or instruction),
        scholarly_queries=queries[:3] or [instruction],
        web_query=str(data.get("web_query") or ""),
    )


@dataclass
class Selection:
    candidate: Candidate
    reasoning: str
    from_anchor: int | None


async def rank(
    provider: Provider,
    question: str,
    instruction: str,
    anchors: Sequence[tuple[Paper, NodeReview]],
    rejected: Sequence[tuple[Paper, NodeReview]],
    candidates: list[Candidate],
    budget: int,
) -> list[Selection]:
    """Choose at most `budget` candidates, each with why it was retrieved."""
    shown = candidates[:MAX_RANKED_CANDIDATES]
    lines = [f"NUCLEUS QUESTION: {question}", f"INSTRUCTION: {instruction}",
             f"CHOOSE AT MOST: {budget}"]
    if anchors:
        lines.append("ANCHORS:")
        lines += [f"  [{i}] {_paper_label(p)}" for i, (p, _r) in enumerate(anchors)]
    if rejected:
        lines.append("REJECTED:")
        lines += [f"  - {_paper_label(p)} — reason: {r.rejection_reason or 'none given'}"
                  for p, r in rejected]
    lines.append("CANDIDATES:")
    for i, c in enumerate(shown):
        about = c.snippet or (c.abstract or "")[:ABSTRACT_CHARS_FOR_RANKING]
        authors = ", ".join(c.authors[:3]) or "unknown authors"
        lines.append(f"[{i}] ({c.source}) {c.title} — {authors}, {c.year or 'n.d.'}"
                     f"{f', {c.venue}' if c.venue else ''}\n    {about}")

    _, tool_result = await provider.complete(
        messages=[Message(role="user", content="\n".join(lines))],
        system=ARCHIVIST_RANK_SYSTEM,
        max_tokens=2048,
        tools=[ARCHIVIST_RANK_TOOL],
    )
    if tool_result is None:
        raise RuntimeError("ranking: the model returned no selection")

    chosen: list[Selection] = []
    seen: set[int] = set()
    for entry in tool_result["input"].get("selections") or []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("candidate_index")
        # Out-of-range, repeated, or beyond budget: dropped rather than trusted.
        if not isinstance(index, int) or not 0 <= index < len(shown) or index in seen:
            continue
        seen.add(index)
        anchor = entry.get("from_anchor")
        chosen.append(Selection(
            candidate=shown[index],
            reasoning=str(entry.get("selection_reasoning") or "").strip(),
            from_anchor=anchor if isinstance(anchor, int) and 0 <= anchor < len(anchors) else None,
        ))
        if len(chosen) >= budget:
            break
    return chosen


async def write_relevance(
    provider: Provider, question: str, paper: Paper, quotes: Sequence[tuple[str, str]]
) -> str | None:
    """The relevance statement for one paper, from its evidence only.
    `quotes` is (source, verbatim) pairs. None if the model gives nothing."""
    lines = [f"NUCLEUS QUESTION: {question}", f"PAPER: {_paper_label(paper)}", "QUOTES:"]
    lines += [f"  [{i}] ({source}) \"{text}\"" for i, (source, text) in enumerate(quotes)]
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content="\n".join(lines))],
        system=ARCHIVIST_RELEVANCE_SYSTEM,
        max_tokens=512,
        tools=[ARCHIVIST_RELEVANCE_TOOL],
    )
    if tool_result is None:
        return None
    statement = str(tool_result["input"].get("relevance_statement") or "").strip()
    return statement or None


# ── Database helpers ─────────────────────────────────────────────────────────


async def get_or_create_paper(session: AsyncSession, candidate: Candidate) -> Paper:
    """The Paper row for a candidate: the library's own row, an existing row
    with the same DOI or OpenAlex id, or a new metadata-only ('ghost') row.
    Fills in a missing abstract from the candidate, which only adds metadata."""
    paper: Paper | None = None
    if candidate.paper_id:
        paper = await session.get(Paper, candidate.paper_id)
    if paper is None and candidate.doi:
        paper = await session.scalar(
            select(Paper).where(func.lower(Paper.doi) == candidate.doi.lower())
        )
    if paper is None and candidate.openalex_id:
        paper = await session.scalar(
            select(Paper).where(Paper.openalex_id == candidate.openalex_id)
        )
    if paper is not None:
        if not paper.abstract and candidate.abstract:
            paper.abstract = candidate.abstract
        return paper

    paper = Paper(
        id=uuid.uuid4(), doi=candidate.doi, openalex_id=candidate.openalex_id,
        title=candidate.title, authors_json=[{"name": a} for a in candidate.authors] or None,
        venue=candidate.venue, year=candidate.year, abstract=candidate.abstract,
        status="ghost",
    )
    session.add(paper)
    await session.flush()
    return paper


async def _nucleus_context(
    session: AsyncSession, nucleus: Nucleus, prompt: NucleusPrompt
) -> tuple[list[tuple[Paper, NodeReview]], list[Paper], list[tuple[Paper, NodeReview]],
           set[str], list[tuple[CorpusNode, Paper]]]:
    """Anchors, pinned papers, rejected papers, the dedupe keys of every paper
    already in this nucleus, and the anchor nodes themselves."""
    rows = (await session.execute(
        select(NodeReview, CorpusNode, Paper)
        .join(CorpusNode, CorpusNode.id == NodeReview.node_id)
        .join(Paper, Paper.id == CorpusNode.paper_id)
        .where(NodeReview.nucleus_id == nucleus.id)
    )).all()

    anchor_ids = set(prompt.anchor_node_ids or [])
    anchors: list[tuple[Paper, NodeReview]] = []
    anchor_nodes: list[tuple[CorpusNode, Paper]] = []
    pinned: list[Paper] = []
    rejected: list[tuple[Paper, NodeReview]] = []
    member_keys: set[str] = set()
    for review, node, paper in rows:
        member_keys.add(Candidate(source=v.LIBRARY, title=paper.title, doi=paper.doi,
                                  openalex_id=paper.openalex_id).key)
        if node.id in anchor_ids:
            anchors.append((paper, review))
            anchor_nodes.append((node, paper))
        if review.status == v.PINNED:
            pinned.append(paper)
        elif review.status == v.REJECTED:
            rejected.append((paper, review))
    return anchors, pinned, rejected, member_keys, anchor_nodes


async def _cancelled(session: AsyncSession, expansion: NucleusExpansion) -> bool:
    await session.refresh(expansion, ["status"])
    return expansion.status == v.CANCELLED


# ── The run ──────────────────────────────────────────────────────────────────


@dataclass
class _Tally:
    placed: int = 0
    by_source: dict[str, int] | None = None
    needs_full_text: int = 0
    fetching_in_background: int = 0
    no_evidence: int = 0
    unsupported: int = 0


async def run_expansion(
    session: AsyncSession, expansion_id: uuid.UUID, deps: ArchivistDeps,
    requesting_user_id: uuid.UUID | None = None,
) -> None:
    """Run one expansion to completion, cancellation, or failure."""
    expansion = await session.get(NucleusExpansion, expansion_id)
    if expansion is None:
        log.error("archivist.expansion_not_found", expansion_id=str(expansion_id))
        return
    nucleus = await session.get(Nucleus, expansion.nucleus_id)
    prompt = await session.get(NucleusPrompt, expansion.prompt_id)
    assert nucleus is not None and prompt is not None
    emitter = Emitter(session, expansion, deps)

    try:
        await _run(session, expansion, nucleus, prompt, deps, emitter, requesting_user_id)
    except ExpansionCancelled:
        expansion.status = v.CANCELLED
        expansion.finished_at = datetime.now(UTC)
        await emitter.emit("awaiting_review", "completed",
                           message="Cancelled. Papers already completed are kept.")
    except Exception as exc:
        log.exception("archivist.failed", expansion_id=str(expansion_id))
        await session.rollback()
        expansion = await session.get(NucleusExpansion, expansion_id)
        assert expansion is not None
        expansion.status = v.FAILED
        expansion.finished_at = datetime.now(UTC)
        await emitter.emit(expansion.stage or "interpreting", "failed", message=str(exc)[:300])


async def _run(
    session: AsyncSession, expansion: NucleusExpansion, nucleus: Nucleus,
    prompt: NucleusPrompt, deps: ArchivistDeps, emitter: Emitter,
    requesting_user_id: uuid.UUID | None,
) -> None:
    anchors, pinned, rejected, member_keys, anchor_nodes = await _nucleus_context(
        session, nucleus, prompt
    )

    # ── interpreting ──
    await emitter.emit("interpreting", "current")
    plan = await interpret(deps.archivist, nucleus.question, prompt.text, anchors, pinned,
                           rejected)
    if plan.asks_for_synthesis:
        # The guardrail: decline briefly and point at what's already here,
        # rather than answering the question.
        kept = [p for p, _r in anchors] + list(pinned)
        accepted = (await session.execute(
            select(Paper).join(CorpusNode, CorpusNode.paper_id == Paper.id)
            .join(NodeReview, NodeReview.node_id == CorpusNode.id)
            .where(NodeReview.nucleus_id == nucleus.id,
                   NodeReview.status.in_([v.ACCEPTED, v.PINNED]))
        )).scalars().all()
        relevant = list({p.id: p for p in [*kept, *accepted]}.values())
        listing = "; ".join(_paper_label(p) for p in relevant[:10])
        expansion.reply_text = (plan.decline_reply or
                                "Interpreting the evidence is yours to do.") + (
            f" Papers in this nucleus that bear on it: {listing}." if listing else "")
        await emitter.emit("interpreting", "completed", message="Declined to synthesize.")
        await _finish(emitter, expansion)
        return
    await emitter.emit("interpreting", "completed",
                       message="; ".join(plan.scholarly_queries))
    if await _cancelled(session, expansion):
        raise ExpansionCancelled

    # ── retrieving ──
    await emitter.emit("retrieving", "current")
    library = await deps.library(session, nucleus.project_id, plan.library_query)
    openalex = await deps.openalex(plan.scholarly_queries)
    web = await deps.web(plan.web_query)
    candidates = merge_candidates(library, openalex, web.candidates)
    await emitter.emit("retrieving", "completed", counts={
        "retrieved": len(candidates), "library": len(library), "openalex": len(openalex),
        "web": len(web.candidates), "web_unmatched": web.unmatched,
    })
    if await _cancelled(session, expansion):
        raise ExpansionCancelled

    # ── ranking ──
    await emitter.emit("ranking", "current")
    rejected_keys = {Candidate(source=v.LIBRARY, title=p.title, doi=p.doi,
                               openalex_id=p.openalex_id).key for p, _r in rejected}
    fresh = [c for c in candidates if c.key not in member_keys]
    selections = (await rank(deps.archivist, nucleus.question, prompt.text, anchors,
                             rejected, fresh, expansion.budget)) if fresh else []
    await emitter.emit("ranking", "completed", counts={
        "candidates": len(candidates),
        "already_in_nucleus": len(candidates) - len(fresh),
        "rejected_skipped": sum(1 for c in candidates if c.key in rejected_keys),
        "selected": len(selections),
    })

    # ── per paper ──
    tally = _Tally(by_source={})
    audit_run: AuditRun | None = None
    finding_ord = 0
    total = len(selections)
    done = dict.fromkeys(PER_PAPER_STAGES, 0)
    skipped = dict.fromkeys(PER_PAPER_STAGES, 0)
    query = nucleus.question if prompt.text.strip() == nucleus.question.strip() \
        else f"{nucleus.question} — {prompt.text}"

    async def passed(stage: str, *, skip: bool = False) -> None:
        done[stage] += 1
        if skip:
            skipped[stage] += 1
        counts: dict[str, Any] = {"done": done[stage], "of": total}
        if skipped[stage]:
            counts["skipped"] = skipped[stage]
        await emitter.emit(stage, "completed" if done[stage] == total else "current",
                           counts=counts)

    async def skip_rest(after: str) -> None:
        for stage in PER_PAPER_STAGES[PER_PAPER_STAGES.index(after) + 1:]:
            await passed(stage, skip=True)

    for selection in selections:
        if await _cancelled(session, expansion):
            raise ExpansionCancelled
        candidate = selection.candidate
        paper = await get_or_create_paper(session, candidate)
        from_node = None
        if selection.from_anchor is not None:
            from_node = anchor_nodes[selection.from_anchor][0].id
        elif len(anchor_nodes) == 1:
            from_node = anchor_nodes[0][0].id
        try:
            placement = await place_node(
                session, nucleus, paper.id, from_node_id=from_node,
                prompt_id=prompt.id, retrieval_source=candidate.source,
            )
        except RejectedUnderThisNucleus:
            # Filtered before ranking; kept as a guard, never proposed.
            continue
        review = placement.review
        review.selection_reasoning = selection.reasoning or None
        tally.placed += 1
        assert tally.by_source is not None
        tally.by_source[candidate.source] = tally.by_source.get(candidate.source, 0) + 1
        await emitter.emit("ingesting", v.NODE_FETCHING, node_id=placement.node.id)

        # ingesting: what text is already in hand, and — for a paper without
        # full text — whether a legal open-access PDF can be fetched later.
        in_library = candidate.source == v.LIBRARY and paper.status == "full_text"
        has_abstract = bool(paper.abstract)
        fetching = False
        if not in_library and paper.status != "full_text":
            fetching = await request_background_fulltext(deps, paper)
            if fetching:
                tally.fetching_in_background += 1
        await passed("ingesting")
        if not in_library and not has_abstract:
            if fetching:
                # Not "user must supply": its PDF is on the way, and the node
                # will gain evidence and a statement when it lands.
                await emitter.emit("ingesting", v.NODE_FETCHING, node_id=placement.node.id,
                                   message="Fetching the open-access PDF in the background.")
            else:
                tally.needs_full_text += 1
                await emitter.emit("ingesting", v.NODE_NEEDS_FULL_TEXT,
                                   node_id=placement.node.id,
                                   message="No full text, abstract, or open-access copy found.")
            await skip_rest("ingesting")
            continue

        # extracting
        await emitter.emit("extracting", v.NODE_EXTRACTING, node_id=placement.node.id)
        evidence: list[tuple[str, str, dict[str, str], uuid.UUID | None]] = []
        if in_library:
            passage = await search_full_text_for_claim(
                deps.archivist, session, nucleus.project_id, paper.id, query, deps.embed_model
            )
            if passage is not None and passage.text_quote_selector:
                evidence.append((v.FULL_TEXT, passage.verbatim_text,
                                 passage.text_quote_selector, passage.id))
        if not evidence and paper.abstract:
            matches = await rank_candidates(deps.archivist, query, [FindCandidate(
                title=paper.title, creators=candidate.authors, year=paper.year,
                venue=paper.venue, doi=paper.doi, abstract=paper.abstract, source=candidate,
            )])
            if matches:
                selector = text_quote_selector_in(matches[0].quote, paper.abstract)
                if selector is not None:
                    evidence.append((v.ABSTRACT, matches[0].quote, selector, None))
        await passed("extracting")
        if not evidence:
            # Text in hand, but nothing in it supports a statement. No
            # statement is written — one without evidence could only be a guess.
            tally.no_evidence += 1
            await emitter.emit("extracting", v.NODE_READY, node_id=placement.node.id,
                               message="No verbatim evidence found; no relevance statement.")
            await skip_rest("extracting")
            await session.commit()
            continue

        # interpreting_papers
        statement = await write_relevance(
            deps.archivist, nucleus.question, paper, [(s, t) for s, t, _sel, _pid in evidence]
        )
        await passed("interpreting_papers")
        if statement is None:
            tally.no_evidence += 1
            await emitter.emit("interpreting_papers", v.NODE_READY, node_id=placement.node.id,
                               message="No relevance statement could be written.")
            await skip_rest("interpreting_papers")
            await session.commit()
            continue
        review.relevance_text = statement
        review.relevance_provenance = v.AI_PROPOSED
        for ord_, (source, text, selector, passage_id) in enumerate(evidence):
            session.add(NodeEvidence(
                id=uuid.uuid4(), review_id=review.id, ord=ord_, source=source,
                verbatim_text=text, text_quote_selector=selector, passage_id=passage_id,
            ))
        await session.flush()

        # verifying — the same engine as the Audit tab, its own kind of run
        await emitter.emit("verifying", v.NODE_VERIFYING, node_id=placement.node.id)
        if audit_run is None:
            audit_run = AuditRun(
                id=uuid.uuid4(), project_id=nucleus.project_id, kind="nucleate_node",
                expansion_id=expansion.id, created_by=requesting_user_id, status="running",
                model=deps.checker_model, quotes_checked=0, malformed_entries=0,
            )
            session.add(audit_run)
            await session.flush()
        checked = await audit_evidence(
            checker=deps.checker, assertion=statement,
            quotes=[t for _s, t, _sel, _pid in evidence], checker_model_name=deps.checker_model,
        )
        audit_run.quotes_checked = (audit_run.quotes_checked or 0) + len(evidence)
        audit_run.malformed_entries = (audit_run.malformed_entries or 0) + \
            checked.malformed_entries
        for k, (supported, rationale) in enumerate(checked.results):
            session.add(AuditFinding(
                id=uuid.uuid4(), audit_run_id=audit_run.id, ord=finding_ord,
                node_review_id=review.id, sub_id=str(k + 1), quote=evidence[k][1],
                supported=supported, rationale=rationale,
            ))
            finding_ord += 1
        # Shown, never hidden: a failing statement is marked, and the user decides.
        if not any(supported for supported, _r in checked.results):
            tally.unsupported += 1
        await passed("verifying")

        # placing
        await passed("placing")
        await emitter.emit("placing", v.NODE_READY, node_id=placement.node.id)

    if audit_run is not None:
        audit_run.status = "done"
    expansion.reply_text = _summary(tally)
    await _finish(emitter, expansion)


def _summary(tally: _Tally) -> str:
    """The chat reply. Counts and sources only — it describes what was done,
    and never what the papers mean (handoff Part III)."""
    if not tally.placed:
        return "Found no new papers for this instruction."
    by_source = tally.by_source or {}
    parts = [f"{n} from {'your library' if s == v.LIBRARY else s}"
             for s, n in by_source.items() if n]
    lines = [f"Proposed {tally.placed} paper{'s' if tally.placed != 1 else ''}"
             f" ({', '.join(parts)})."]
    if tally.fetching_in_background:
        n = tally.fetching_in_background
        lines.append(f"{n} open-access PDF{'s are' if n != 1 else ' is'} being fetched in the "
                     "background; those nodes will gain full-text evidence when it lands.")
    if tally.needs_full_text:
        lines.append(f"{tally.needs_full_text} need full text before any evidence can be "
                     "drawn from them.")
    if tally.no_evidence:
        lines.append(f"{tally.no_evidence} had text but no verbatim evidence for this "
                     "question, so no relevance statement was written.")
    if tally.unsupported:
        lines.append(f"{tally.unsupported} relevance statement"
                     f"{'s were' if tally.unsupported != 1 else ' was'} not supported by "
                     "its own evidence on audit — marked for your review.")
    return " ".join(lines)


async def _finish(emitter: Emitter, expansion: NucleusExpansion) -> None:
    expansion.status = v.DONE
    expansion.finished_at = datetime.now(UTC)
    await emitter.emit("awaiting_review", "completed")
