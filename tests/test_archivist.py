"""End-to-end tests for one Nucleate expansion, against the real database.

The model is scripted and the retrieval sources are stubs, so every stage runs
for real — placement, evidence with selectors, relevance,
the event log, cost metering, cancellation — without a paid call or a network.
The model's answers are keyed by which tool it was asked to use, which is how
the pipeline asks it for anything.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from athenaeum.domain import nucleate as v
from athenaeum.nucleate import archivist
from athenaeum.nucleate.archivist import ArchivistDeps, rank, run_expansion
from athenaeum.nucleate.corpus import place_node
from athenaeum.nucleate.retrieval import Candidate, OpenAlexResult, WebResult
from athenaeum.storage.models import (
    AuditRun,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusExpansionEvent,
    NucleusPrompt,
    Paper,
    Project,
    User,
)
from athenaeum.synthesize import prompts

QUESTION = "How quickly do microbes grow in soil?"


# ── Scripted model and stub sources ──────────────────────────────────────────


def _first_sentence_of_abstract(content: str) -> str:
    """The abstract ranker's prompt carries 'Abstract: ...'; answer with its
    first sentence, which is a genuine verbatim substring."""
    match = re.search(r"Abstract: (.+?\.)", content)
    return match.group(1) if match else ""


class ScriptedProvider:
    """Answers each tool call from a table keyed by tool name, and meters a
    fixed number of tokens per call so cost accounting can be checked."""

    def __init__(self, **overrides: Any) -> None:
        self.calls: list[str] = []
        self.prompts: dict[str, list[str]] = {}
        self.input_tokens = 0
        self.output_tokens = 0
        self.handlers: dict[str, Any] = {
            "plan_retrieval": lambda c: {
                "asks_for_synthesis": False, "library_query": "growth rates",
                "scholarly_queries": ["soil microbial growth rate"],
                "web_query": "soil microbial growth rate"},
            "select_papers": lambda c: {"selections": [
                {"candidate_index": 0, "selection_reasoning": "Measures growth directly."},
                {"candidate_index": 1, "selection_reasoning": "Uses the named method."}]},
            "select_relevant_items": lambda c: {"results": [
                {"candidate_index": 0, "quote": _first_sentence_of_abstract(c),
                 "relevance_note": "on point"}]},
            "write_relevance": lambda c: {
                "relevance_statement": "The authors estimate taxon-specific growth rates."},
            "audit_evidence": lambda c: {"results": [
                {"evidence_index": 0, "supported": True, "rationale": "Stated directly."}]},
        }
        self.handlers.update(overrides)

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        name = tools[0]["name"]
        self.calls.append(name)
        self.prompts.setdefault(name, []).append(messages[0].content)
        self.input_tokens += 100
        self.output_tokens += 20
        result = self.handlers[name](messages[0].content)
        return None, (None if result is None else {"name": name, "input": result})


def _candidate(title: str, abstract: str | None, source: str = v.OPENALEX) -> Candidate:
    return Candidate(source=source, title=title, authors=["A. Koch"], year=2018,
                     venue="Nature", doi=f"10.9999/{uuid.uuid4().hex[:10]}",
                     openalex_id=f"https://openalex.org/W{uuid.uuid4().int % 10**9}",
                     abstract=abstract)


def _deps(provider, *, openalex=(), library=(), web=None, events=None) -> ArchivistDeps:
    published = events if events is not None else []

    async def _library(session, project_id, query):
        return list(library)

    async def _openalex(queries):
        return OpenAlexResult(candidates=list(openalex), failed_queries=0)

    async def _web(query):
        return web or WebResult(candidates=[], unmatched=0)

    async def _publish(event):
        published.append(event)

    return ArchivistDeps(archivist=provider,
                         embed_model="unused", library=_library, openalex=_openalex,
                         web=_web, publish=_publish)


async def _world(session, *, budget: int = 6, prompt_text: str = QUESTION):
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0002",
                display_name="Archivist Tester")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Soil growth", owner_id=user.id)
    session.add(project)
    await session.flush()
    nucleus = Nucleus(id=uuid.uuid4(), project_id=project.id, question=QUESTION)
    session.add(nucleus)
    await session.flush()
    prompt = NucleusPrompt(id=uuid.uuid4(), nucleus_id=nucleus.id, text=prompt_text)
    session.add(prompt)
    await session.flush()
    expansion = NucleusExpansion(id=uuid.uuid4(), nucleus_id=nucleus.id, prompt_id=prompt.id,
                                 budget=budget)
    session.add(expansion)
    await session.flush()
    return user, nucleus, prompt, expansion


_KOCH = "Growth rates spanned two orders of magnitude. Rates were measured by DNA-SIP."
_MILLER = "Microbial turnover was slow in dry soils. Moisture controlled activity."


async def _reviews(session, nucleus_id):
    return list(await session.scalars(
        select(NodeReview).where(NodeReview.nucleus_id == nucleus_id)
    ))


async def _events(session, expansion_id):
    return list(await session.scalars(
        select(NucleusExpansionEvent).where(NucleusExpansionEvent.expansion_id == expansion_id)
        .order_by(NucleusExpansionEvent.ord)
    ))


# ── The whole run ────────────────────────────────────────────────────────────


async def test_an_expansion_places_nodes_with_evidence_and_statements(db_session) -> None:
    user, nucleus, _, expansion = await _world(db_session)
    provider = ScriptedProvider()
    deps = _deps(provider, openalex=[_candidate("Koch growth rates", _KOCH),
                                     _candidate("Miller turnover", _MILLER)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    await db_session.refresh(expansion)
    assert expansion.status == v.DONE
    reviews = await _reviews(db_session, nucleus.id)
    assert len(reviews) == 2
    for review in reviews:
        assert review.status == v.FRONTIER
        assert review.relevance_provenance == v.AI_PROPOSED
        assert review.relevance_text
        assert review.selection_reasoning
        evidence = list(await db_session.scalars(
            select(NodeEvidence).where(NodeEvidence.review_id == review.id)))
        assert len(evidence) == 1
        assert evidence[0].source == v.ABSTRACT
        # Every quote anchored, and the anchor really describes the quote.
        assert evidence[0].text_quote_selector["exact"] == evidence[0].verbatim_text

    # No automatic audit any more (D-N22): no second model call, no run record.
    assert await db_session.scalar(
        select(AuditRun).where(AuditRun.expansion_id == expansion.id)) is None
    assert "audit_evidence" not in provider.calls
    assert "Proposed 2 papers (2 from OpenAlex)." in expansion.reply_text


async def test_stages_are_reported_in_order_with_real_counts(db_session) -> None:
    user, _, _, expansion = await _world(db_session)
    deps = _deps(ScriptedProvider(), openalex=[_candidate("Koch", _KOCH),
                                               _candidate("Miller", _MILLER)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    events = await _events(db_session, expansion.id)
    stage_order = []
    for e in events:
        if e.node_id is None and e.stage not in stage_order:
            stage_order.append(e.stage)
    assert stage_order == list(v.ACTIVE_STAGES)
    retrieved = next(e for e in events if e.stage == "retrieving" and e.status == "completed")
    assert retrieved.counts["openalex"] == 2
    ranked = next(e for e in events if e.stage == "ranking" and e.status == "completed")
    assert ranked.counts["selected"] == 2
    placing = [e for e in events if e.stage == "placing" and e.node_id is None]
    assert [e.counts for e in placing] == [{"done": 1, "of": 2}, {"done": 2, "of": 2}]
    assert placing[-1].status == "completed"
    # "Awaiting review" is an explicit final state, not the absence of a spinner.
    assert (events[-1].stage, events[-1].status) == ("awaiting_review", "completed")


async def test_each_node_reports_its_own_status_as_it_goes(db_session) -> None:
    user, _, _, expansion = await _world(db_session)
    deps = _deps(ScriptedProvider(), openalex=[_candidate("Koch", _KOCH)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    per_node = [e.status for e in await _events(db_session, expansion.id)
                if e.node_id is not None]
    assert per_node == [v.NODE_FETCHING, v.NODE_EXTRACTING, v.NODE_READY]


async def test_tokens_are_metered_onto_the_run_and_attributed_to_events(db_session) -> None:
    user, _, _, expansion = await _world(db_session)
    provider = ScriptedProvider()
    deps = _deps(provider, openalex=[_candidate("Koch", _KOCH)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    await db_session.refresh(expansion)
    events = await _events(db_session, expansion.id)
    # The scripted model charges 100 in / 20 out per call; one provider plays
    # both roles and must be counted once.
    assert expansion.input_tokens == 100 * len(provider.calls)
    assert expansion.output_tokens == 20 * len(provider.calls)
    assert sum(e.input_tokens for e in events) == expansion.input_tokens
    interpreting = next(e for e in events if e.stage == "interpreting" and e.status == "completed")
    assert interpreting.input_tokens == 100  # the planning call, attributed to its stage


# ── The guardrail ────────────────────────────────────────────────────────────


async def test_a_request_to_synthesize_is_declined_without_retrieving(db_session) -> None:
    user, nucleus, _, expansion = await _world(db_session, prompt_text="So what's the answer?")
    decline = "Interpreting the evidence is yours; I can find or describe papers instead."
    provider = ScriptedProvider(plan_retrieval=lambda c: {
        "asks_for_synthesis": True, "decline_reply": decline})
    deps = _deps(provider, openalex=[_candidate("Koch", _KOCH)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    await db_session.refresh(expansion)
    assert expansion.status == v.DONE
    assert expansion.reply_text.startswith(decline)
    assert provider.calls == ["plan_retrieval"], "nothing retrieved, ranked or written"
    assert await _reviews(db_session, nucleus.id) == []


def test_every_archivist_prompt_opens_with_the_guardrail() -> None:
    for system in (prompts.ARCHIVIST_INTERPRET_SYSTEM, prompts.ARCHIVIST_RANK_SYSTEM,
                   prompts.ARCHIVIST_RELEVANCE_SYSTEM):
        assert system.startswith(prompts.ARCHIVIST_GUARDRAIL)
    assert "do NOT" in prompts.ARCHIVIST_GUARDRAIL
    assert "answer the research question" in prompts.ARCHIVIST_GUARDRAIL


# ── Steering ─────────────────────────────────────────────────────────────────


async def test_a_paper_rejected_here_is_never_reproposed(db_session) -> None:
    user, nucleus, _, expansion = await _world(db_session)
    # A title nothing else in the prompt can contain (every test candidate
    # shares the author "A. Koch", so a bare surname would prove nothing).
    rejected = _candidate("Rejected-alpine-study-7Q", _KOCH)
    paper = Paper(id=uuid.uuid4(), title=rejected.title, doi=rejected.doi, status="ghost",
                  abstract=_KOCH)
    db_session.add(paper)
    await db_session.flush()
    earlier = await place_node(db_session, nucleus, paper.id)  # added by a person
    earlier.review.status = v.REJECTED
    earlier.review.rejection_reason = "wrong ecosystem"
    await db_session.flush()

    provider = ScriptedProvider(select_papers=lambda c: {"selections": [
        {"candidate_index": 0, "selection_reasoning": "only candidate shown"}]})
    deps = _deps(provider, openalex=[rejected, _candidate("Miller", _MILLER)])
    await run_expansion(db_session, expansion.id, deps, user.id)

    events = await _events(db_session, expansion.id)
    ranked = next(e for e in events if e.stage == "ranking" and e.status == "completed")
    assert ranked.counts["rejected_skipped"] == 1
    # place_node would refuse it anyway; this proves it was filtered *before*
    # ranking, so the model is never shown — or charged for — a paper the
    # researcher already rejected here.
    (ranking_prompt,) = provider.prompts["select_papers"]
    shown_candidates = ranking_prompt.split("CANDIDATES:", 1)[1]
    assert rejected.title not in shown_candidates
    assert "Miller" in shown_candidates
    reviews = {r.node_id: r for r in await _reviews(db_session, nucleus.id)}
    assert reviews[earlier.node.id].status == v.REJECTED, "never reopened"
    assert len(reviews) == 2  # the rejected one, plus Miller — never Koch again


# ── Honest outcomes ──────────────────────────────────────────────────────────


async def test_a_paper_with_no_text_needs_full_text_and_gets_no_statement(db_session) -> None:
    user, nucleus, _, expansion = await _world(db_session)
    deps = _deps(ScriptedProvider(select_papers=lambda c: {"selections": [
        {"candidate_index": 0, "selection_reasoning": "title matches"}]}),
        openalex=[_candidate("No abstract anywhere", None)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    (review,) = await _reviews(db_session, nucleus.id)
    assert review.relevance_text is None, "no statement without evidence to support it"
    events = await _events(db_session, expansion.id)
    assert any(e.status == v.NODE_NEEDS_FULL_TEXT for e in events)
    # The remaining per-paper stages still complete, counting the skip.
    placing = [e for e in events if e.stage == "placing" and e.node_id is None]
    assert placing[-1].counts == {"done": 1, "of": 1, "skipped": 1}
    await db_session.refresh(expansion)
    assert "1 needs full text before any evidence can be drawn from it." in expansion.reply_text


async def test_web_pages_matching_no_paper_are_counted_not_guessed(db_session) -> None:
    user, _, _, expansion = await _world(db_session)
    deps = _deps(ScriptedProvider(),
                 openalex=[_candidate("Koch", _KOCH), _candidate("Miller", _MILLER)],
                 web=WebResult(candidates=[], unmatched=3))

    await run_expansion(db_session, expansion.id, deps, user.id)

    events = await _events(db_session, expansion.id)
    retrieved = next(e for e in events if e.stage == "retrieving" and e.status == "completed")
    assert retrieved.counts["web_unmatched"] == 3


# ── Cancel and failure ───────────────────────────────────────────────────────


async def test_cancelling_keeps_completed_nodes_and_stops(db_session, monkeypatch) -> None:
    user, nucleus, _, expansion = await _world(db_session)
    # Cancelled checks happen after interpreting, after retrieving, then before
    # each paper; the fourth check is the one before the second paper.
    checks = {"n": 0}

    async def _cancel_before_second_paper(session, exp):
        checks["n"] += 1
        return checks["n"] >= 4

    monkeypatch.setattr(archivist, "_cancelled", _cancel_before_second_paper)
    deps = _deps(ScriptedProvider(), openalex=[_candidate("Koch", _KOCH),
                                               _candidate("Miller", _MILLER)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    await db_session.refresh(expansion)
    assert expansion.status == v.CANCELLED
    reviews = await _reviews(db_session, nucleus.id)
    assert len(reviews) == 1, "the first paper was completed and is kept"
    assert reviews[0].relevance_text is not None


async def test_a_failure_marks_the_run_failed_with_a_reason(db_session) -> None:
    user, _, _, expansion = await _world(db_session)
    deps = _deps(ScriptedProvider(plan_retrieval=lambda c: None))

    await run_expansion(db_session, expansion.id, deps, user.id)

    await db_session.refresh(expansion)
    assert expansion.status == v.FAILED
    last = (await _events(db_session, expansion.id))[-1]
    assert last.status == "failed" and "no plan" in (last.message or "")


# ── Ranking is not trusted blindly ───────────────────────────────────────────


async def test_rank_drops_invalid_and_repeated_choices_and_honours_the_budget() -> None:
    cands = [_candidate(f"Paper {i}", _KOCH) for i in range(4)]
    provider = ScriptedProvider(select_papers=lambda c: {"selections": [
        {"candidate_index": 9, "selection_reasoning": "out of range"},
        {"candidate_index": 1, "selection_reasoning": "ok"},
        {"candidate_index": 1, "selection_reasoning": "repeat"},
        {"candidate_index": 2, "selection_reasoning": "ok"},
        {"candidate_index": 3, "selection_reasoning": "over budget"},
    ]})

    chosen = await rank(provider, QUESTION, QUESTION, [], [], cands, budget=2)

    assert [s.candidate.title for s in chosen] == ["Paper 1", "Paper 2"]


@pytest.mark.parametrize("budget", [v.MIN_EXPANSION_BUDGET, v.MAX_EXPANSION_BUDGET])
def test_default_budget_is_within_the_configured_range(budget) -> None:
    assert v.MIN_EXPANSION_BUDGET <= v.DEFAULT_EXPANSION_BUDGET <= v.MAX_EXPANSION_BUDGET



# ── Found by the first live run ──────────────────────────────────────────────


async def test_a_new_nucleus_is_not_handed_its_question_as_the_instruction(db_session) -> None:
    """The first live run declined a brand-new nucleus as a request to
    synthesize: its opening prompt *is* its question, and "How quickly do
    microbes grow in soil?" reads as "please answer this". The opening step
    must be told what it actually asks — find papers."""
    user, _, _, expansion = await _world(db_session)  # prompt text == the question
    provider = ScriptedProvider()
    deps = _deps(provider, openalex=[_candidate("Koch", _KOCH)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    (planning,) = provider.prompts["plan_retrieval"]
    assert f"INSTRUCTION: {archivist.OPENING_INSTRUCTION}" in planning
    assert f"INSTRUCTION: {QUESTION}" not in planning
    assert f"NUCLEUS QUESTION: {QUESTION}" in planning


async def test_a_follow_up_prompt_is_passed_through_unchanged(db_session) -> None:
    user, _, _, expansion = await _world(db_session, prompt_text="Look outside soil too")
    provider = ScriptedProvider()
    deps = _deps(provider, openalex=[_candidate("Koch", _KOCH)])

    await run_expansion(db_session, expansion.id, deps, user.id)

    (planning,) = provider.prompts["plan_retrieval"]
    assert "INSTRUCTION: Look outside soil too" in planning


def test_the_interpret_prompt_says_the_question_is_never_itself_a_synthesis_request() -> None:
    system = prompts.ARCHIVIST_INTERPRET_SYSTEM
    assert "Judge the\ninstruction only, never the nucleus question" in system
    assert "never, by itself, a\nrequest for you to answer it" in system
