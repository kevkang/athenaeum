"""Tests for D-N15: finding open-access PDFs during an expansion, and bringing
nodes up to full-text evidence once a PDF has been parsed.

The model is scripted and the full-text search is stubbed with one that writes
a real Passage, so every rule runs against the real database. Each upgrade
rule has its own test because each one guards against a quiet failure: paying
for rejected papers, duplicating evidence on a re-run, writing an AI statement
nothing can audit, or auditing a person's own words.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from athenaeum.domain import nucleate as v
from athenaeum.nucleate import fulltext
from athenaeum.nucleate.archivist import ArchivistDeps, request_background_fulltext, run_expansion
from athenaeum.nucleate.corpus import place_node
from athenaeum.nucleate.fulltext import UpgradeDeps, upgrade_nodes_for_paper
from athenaeum.nucleate.retrieval import Candidate, WebResult
from athenaeum.storage.models import (
    AuditRun,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusPrompt,
    Paper,
    Passage,
    Project,
    User,
)

QUESTION = "How quickly do microbes grow in soil?"
FULLTEXT_QUOTE = "Taxon-specific growth rates were measured over eight weeks."


class _Model:
    """Scripted answers by tool name, recording which were asked for."""

    def __init__(self, supported: bool = True) -> None:
        self.calls: list[str] = []
        self.supported = supported

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        name = tools[0]["name"]
        self.calls.append(name)
        answers: dict[str, Any] = {
            "write_relevance": {"relevance_statement": "Koch et al. measure growth rates."},
            "audit_evidence": {"results": [
                {"evidence_index": i, "supported": self.supported, "rationale": "checked"}
                for i in range(messages[0].content.count("\n[") + 1)]},
            "select_relevant_items": {"results": [
                {"candidate_index": 0, "quote": "Growth rates spanned two orders of magnitude.",
                 "relevance_note": "on point"}]},
            "select_papers": {"selections": [
                {"candidate_index": 0, "selection_reasoning": "measures growth"}]},
            "plan_retrieval": {"asks_for_synthesis": False, "library_query": "growth",
                               "scholarly_queries": ["growth"], "web_query": ""},
        }
        return None, {"name": name, "input": answers[name]}


# ── The open-access check during an expansion ────────────────────────────────


def _oa_deps(found: str | None, calls: dict) -> ArchivistDeps:
    async def find(doi: str) -> str | None:
        calls.setdefault("looked_up", []).append(doi)
        return found

    async def request(paper_id: uuid.UUID, url: str) -> None:
        calls.setdefault("fetched", []).append((paper_id, url))

    async def _unused(*a, **k):
        raise AssertionError("not used here")

    return ArchivistDeps(archivist=_Model(), checker=_Model(), checker_model="c",
                         embed_model="e", library=_unused, openalex=_unused, web=_unused,
                         publish=_unused, find_open_access=find, request_fulltext=request)


def _ghost(**kw) -> Paper:
    fields = {"id": uuid.uuid4(), "title": "Koch", "status": "ghost",
              "doi": f"10.9999/{uuid.uuid4().hex[:8]}"}
    fields.update(kw)
    return Paper(**fields)


async def test_an_open_access_copy_is_recorded_and_requested() -> None:
    calls: dict = {}
    paper = _ghost()

    assert await request_background_fulltext(_oa_deps("https://oa.example/k.pdf", calls), paper)
    assert paper.oa_pdf_url == "https://oa.example/k.pdf"
    assert paper.oa_checked_at is not None
    assert calls["fetched"] == [(paper.id, "https://oa.example/k.pdf")]


async def test_no_open_access_copy_is_recorded_as_checked() -> None:
    """'Checked, nothing found' is what the needs-full-text queue must show,
    so it has to be distinguishable from never checked."""
    calls: dict = {}
    paper = _ghost()

    assert not await request_background_fulltext(_oa_deps(None, calls), paper)
    assert paper.oa_pdf_url is None
    assert paper.oa_checked_at is not None
    assert "fetched" not in calls


async def test_a_recent_check_is_reused_and_a_stale_one_repeated() -> None:
    calls: dict = {}
    recent = _ghost(oa_checked_at=datetime.now(UTC) - timedelta(days=1), oa_pdf_url=None)
    stale = _ghost(oa_checked_at=datetime.now(UTC) - timedelta(days=30), oa_pdf_url=None)

    await request_background_fulltext(_oa_deps("https://oa.example/x.pdf", calls), recent)
    await request_background_fulltext(_oa_deps("https://oa.example/x.pdf", calls), stale)

    assert calls["looked_up"] == [stale.doi], "a day-old answer is reused, a month-old one isn't"


async def test_no_doi_or_no_hooks_means_no_lookup() -> None:
    calls: dict = {}
    assert not await request_background_fulltext(_oa_deps("u", calls), _ghost(doi=None))
    no_hooks = _oa_deps("u", calls)
    no_hooks.find_open_access = None
    assert not await request_background_fulltext(no_hooks, _ghost())
    assert calls == {}


async def _world(session):
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0005",
                display_name="Fulltext Tester")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Fulltext", owner_id=user.id)
    session.add(project)
    await session.flush()
    nucleus = Nucleus(id=uuid.uuid4(), project_id=project.id, question=QUESTION,
                      created_by=user.id)
    session.add(nucleus)
    await session.flush()
    prompt = NucleusPrompt(id=uuid.uuid4(), nucleus_id=nucleus.id, text=QUESTION)
    session.add(prompt)
    await session.flush()
    expansion = NucleusExpansion(id=uuid.uuid4(), nucleus_id=nucleus.id, prompt_id=prompt.id,
                                 budget=6)
    session.add(expansion)
    await session.flush()
    return user, nucleus, prompt, expansion


async def test_a_paper_with_no_abstract_but_an_open_access_copy_is_fetching_not_stuck(
    db_session,
) -> None:
    """Its PDF is on the way, so it must not land in the needs-full-text queue."""
    user, _, _, expansion = await _world(db_session)
    calls: dict = {}
    deps = _oa_deps("https://oa.example/k.pdf", calls)
    events: list = []

    async def openalex(queries):
        return [Candidate(source=v.OPENALEX, title="No abstract",
                          doi=f"10.9999/{uuid.uuid4().hex[:8]}")]

    async def nothing(*a):
        return []

    async def no_web(q):
        return WebResult(candidates=[], unmatched=0)

    async def publish(e):
        events.append(e)

    deps.library, deps.openalex, deps.web, deps.publish = nothing, openalex, no_web, publish

    await run_expansion(db_session, expansion.id, deps, user.id)

    node_statuses = [e["status"] for e in events if e["node_id"]]
    assert v.NODE_NEEDS_FULL_TEXT not in node_statuses
    assert node_statuses[-1] == v.NODE_FETCHING
    assert len(calls["fetched"]) == 1
    await db_session.refresh(expansion)
    assert "being fetched in the background" in expansion.reply_text
    assert "full text before any evidence" not in expansion.reply_text


# ── The upgrade once full text lands ─────────────────────────────────────────


@pytest.fixture
def stub_fulltext_search(monkeypatch):
    """A full-text search that writes a real Passage, and records how it was
    asked to scope itself."""
    seen: dict = {}

    async def _search(provider, session, project_id, paper_id, claim, embed_model,
                      link_to_project=True):
        seen["link_to_project"] = link_to_project
        passage = Passage(
            id=uuid.uuid4(), paper_id=paper_id, sentence_ids=[], verbatim_text=FULLTEXT_QUOTE,
            source_check="matches_source",
            text_quote_selector={"type": "TextQuoteSelector", "exact": FULLTEXT_QUOTE,
                                 "prefix": "", "suffix": ""})
        session.add(passage)
        await session.flush()
        return passage

    monkeypatch.setattr(fulltext, "search_full_text_for_claim", _search)
    return seen


async def _node(session, nucleus, paper, prompt=None, **review_fields) -> NodeReview:
    placed = await place_node(
        session, nucleus, paper.id,
        prompt_id=prompt.id if prompt else None,
        retrieval_source=v.OPENALEX if prompt else None,
    )
    for key, value in review_fields.items():
        setattr(placed.review, key, value)
    await session.flush()
    return placed.review


def _deps_for(model: _Model):
    async def deps_for(nucleus):
        return UpgradeDeps(provider=model, checker=model, checker_model="c", embed_model="e")
    return deps_for


async def _full_text_paper(session) -> Paper:
    paper = _ghost(status="full_text", abstract="Growth rates spanned two orders of magnitude.")
    session.add(paper)
    await session.flush()
    return paper


async def _evidence(session, review) -> list[NodeEvidence]:
    return list(await session.scalars(
        select(NodeEvidence).where(NodeEvidence.review_id == review.id)
        .order_by(NodeEvidence.ord)))


async def test_an_archivist_node_gains_full_text_evidence_and_is_reaudited(
    db_session, stub_fulltext_search,
) -> None:
    _, nucleus, prompt, expansion = await _world(db_session)
    paper = await _full_text_paper(db_session)
    review = await _node(db_session, nucleus, paper, prompt,
                         relevance_text="Koch et al. report growth rates.",
                         relevance_provenance=v.AI_PROPOSED)
    db_session.add(NodeEvidence(
        id=uuid.uuid4(), review_id=review.id, ord=0, source=v.ABSTRACT,
        verbatim_text="Growth rates spanned two orders of magnitude.",
        text_quote_selector={"type": "TextQuoteSelector",
                             "exact": "Growth rates spanned two orders of magnitude."}))
    await db_session.flush()

    outcomes = await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(_Model()))

    assert outcomes == {"evidence_added": 1, "reaudited": 1}
    assert stub_fulltext_search["link_to_project"] is False, "corpus papers aren't in a library"
    evidence = await _evidence(db_session, review)
    assert [(e.ord, e.source) for e in evidence] == [(0, v.ABSTRACT), (1, v.FULL_TEXT)]
    run = await db_session.scalar(select(AuditRun).where(AuditRun.expansion_id == expansion.id))
    assert run is not None and run.kind == "nucleate_node" and run.quotes_checked == 2


async def test_a_node_that_had_no_text_gets_its_statement_now(
    db_session, stub_fulltext_search,
) -> None:
    _, nucleus, prompt, _ = await _world(db_session)
    paper = await _full_text_paper(db_session)
    review = await _node(db_session, nucleus, paper, prompt)

    outcomes = await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(_Model()))

    assert outcomes["statement_written"] == 1 and outcomes["reaudited"] == 1
    assert review.relevance_provenance == v.AI_PROPOSED


async def test_running_twice_changes_nothing_the_second_time(
    db_session, stub_fulltext_search,
) -> None:
    _, nucleus, prompt, _ = await _world(db_session)
    paper = await _full_text_paper(db_session)
    review = await _node(db_session, nucleus, paper, prompt)
    model = _Model()

    await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(model))
    calls_after_first = len(model.calls)
    second = await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(model))

    assert second == {"already_full_text": 1}
    assert len(model.calls) == calls_after_first, "no model spend on a re-run"
    assert len(await _evidence(db_session, review)) == 1


async def test_rejected_nodes_cost_nothing(db_session, stub_fulltext_search) -> None:
    _, nucleus, prompt, _ = await _world(db_session)
    paper = await _full_text_paper(db_session)
    await _node(db_session, nucleus, paper, prompt, status=v.REJECTED)
    model = _Model()

    outcomes = await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(model))

    assert outcomes == {"skipped_rejected": 1}
    assert model.calls == []


async def test_a_persons_node_gets_evidence_but_no_ai_statement(
    db_session, stub_fulltext_search,
) -> None:
    """No expansion introduced it, so an AI statement could not be audited."""
    _, nucleus, _, _ = await _world(db_session)
    paper = await _full_text_paper(db_session)
    review = await _node(db_session, nucleus, paper)  # added by a person

    outcomes = await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(_Model()))

    assert outcomes == {"evidence_added": 1}
    assert review.relevance_text is None
    assert len(await _evidence(db_session, review)) == 1


async def test_a_persons_own_words_are_not_audited(db_session, stub_fulltext_search) -> None:
    _, nucleus, prompt, _ = await _world(db_session)
    paper = await _full_text_paper(db_session)
    await _node(db_session, nucleus, paper, prompt,
                relevance_text="My own reading of this paper.",
                relevance_provenance=v.USER_SET)
    model = _Model()

    outcomes = await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(model))

    assert outcomes == {"evidence_added": 1}
    assert "audit_evidence" not in model.calls


async def test_nothing_happens_until_the_paper_has_full_text(
    db_session, stub_fulltext_search,
) -> None:
    _, nucleus, prompt, _ = await _world(db_session)
    paper = _ghost()
    db_session.add(paper)
    await db_session.flush()
    await _node(db_session, nucleus, paper, prompt)

    assert await upgrade_nodes_for_paper(db_session, paper.id, _deps_for(_Model())) == {}
