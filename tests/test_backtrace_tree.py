"""Integration tests for backtrace's tree-shaped resolution against a real
database — api/backtrace.py's `_rank`/`_commit_hop`, which had zero DB-level
coverage before (test_backtrace.py only unit-tests the pure LLM-facing
functions in synthesize/backtrace.py; test_smoke.py only checks 401s).
Exercises the behavior specific to branching: parent_hop_id wiring, depth
computation, and tree-wide exclusion of already-visited papers — not just
the single-hop resolution logic that test_backtrace.py already covers via
fakes.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.backtrace import _commit_hop, _load_chain_and_hops, _rank
from athenaeum.config import get_settings
from athenaeum.ingest.openalex import OpenAlexMeta
from athenaeum.provider.base import Message
from athenaeum.storage.models import (
    BacktraceChain,
    BacktraceHop,
    Paper,
    Project,
    ProjectMember,
    User,
)
from athenaeum.synthesize import backtrace as synth_backtrace


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        return None, self._tool_result


def _meta(openalex_id: str, abstract: str | None) -> OpenAlexMeta:
    return OpenAlexMeta(
        openalex_id=openalex_id,
        doi=f"10.1234/{openalex_id}",
        title=f"Paper {openalex_id}",
        authors=[{"name": "Ada Borton", "orcid": None}],
        venue="Journal of Tests",
        year=2019,
        abstract=abstract,
        oa_pdf_url=None,
    )


async def _seed_chain(session: AsyncSession) -> tuple[User, Project, BacktraceChain]:
    owner = User(id=uuid.uuid4(), orcid="0000-0000-0000-0002", display_name="Backtrace Tester")
    session.add(owner)
    await session.flush()

    project = Project(id=uuid.uuid4(), title="Backtrace Tree Test Project", owner_id=owner.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))

    origin_paper = Paper(
        id=uuid.uuid4(), openalex_id="W0", title="Origin Paper", status="ghost",
    )
    session.add(origin_paper)
    await session.flush()

    chain = BacktraceChain(
        id=uuid.uuid4(), project_id=project.id, created_by=owner.id,
        origin_paper_id=origin_paper.id,
        origin_text="It has long been believed that bacteria are monoploid organisms.",
    )
    session.add(chain)
    await session.flush()
    return owner, project, chain


def _mock_references(monkeypatch, refs: dict[str, str | None]) -> None:
    """refs maps openalex_id -> abstract (None for no abstract)."""
    monkeypatch.setattr(
        synth_backtrace, "fetch_referenced_work_ids", lambda oid: list(refs.keys())
    )
    monkeypatch.setattr(
        synth_backtrace,
        "fetch_works_by_ids",
        lambda ids: [_meta(oid, refs[oid]) for oid in ids],
    )


# ── _rank ────────────────────────────────────────────────────────────────────

async def test_rank_returns_ranked_candidates_for_origin(db_session, monkeypatch) -> None:
    _owner, _project, chain = await _seed_chain(db_session)
    _mock_references(monkeypatch, {"W1": "abs1", "W2": "abs2"})
    provider = _FakeProvider(
        {"name": "rank_cited_works", "input": {"matches": [
            {"candidate_index": 0, "reasoning": "Plausible match.", "confident": True},
        ]}}
    )

    result = await _rank(
        db_session, get_settings(), provider, None, chain, [], None, None,
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].openalex_id == "W1"
    assert result.candidates[0].confident is True
    assert result.model == get_settings().zotero_find_model


async def test_rank_excludes_already_visited_papers(db_session, monkeypatch) -> None:
    """A paper already in this chain's tree must never be offered again,
    even from a different branch — the LLM should only ever see W2."""
    _owner, _project, chain = await _seed_chain(db_session)
    _mock_references(monkeypatch, {"W1": "abs1", "W2": "abs2"})

    existing_hop = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W1", to_title="Already visited", quote="q", quote_source="abstract",
        resolution_status="abstract_match", user_decision="continued",
    )
    db_session.add(existing_hop)
    await db_session.flush()

    captured: dict = {}

    async def _fake_complete(messages, system, max_tokens=4096, tools=None):
        captured["message"] = messages[0].content
        return None, {"name": "rank_cited_works", "input": {"matches": [
            {"candidate_index": 0, "reasoning": "only one left", "confident": True},
        ]}}

    provider = _FakeProvider(None)
    provider.complete = _fake_complete  # type: ignore[method-assign]

    result = await _rank(
        db_session, get_settings(), provider, None, chain, [existing_hop], None, None,
    )

    assert "W1" not in captured["message"]
    assert len(result.candidates) == 1
    assert result.candidates[0].openalex_id == "W2"


async def test_rank_from_a_specific_parent_hop(db_session, monkeypatch) -> None:
    """Ranking from an existing hop uses *that* hop's paper and quote as the
    citing context, not the chain's origin."""
    _owner, _project, chain = await _seed_chain(db_session)

    parent_hop = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W1", to_title="Parent paper", quote="Methane flux increases with heat.",
        quote_source="abstract", resolution_status="abstract_match", user_decision="pending",
    )
    db_session.add(parent_hop)
    await db_session.flush()

    fetch_referenced_calls = []

    def _fake_fetch_referenced(oid: str) -> list[str]:
        fetch_referenced_calls.append(oid)
        return ["W3"]

    monkeypatch.setattr(synth_backtrace, "fetch_referenced_work_ids", _fake_fetch_referenced)
    monkeypatch.setattr(
        synth_backtrace, "fetch_works_by_ids", lambda ids: [_meta("W3", "abs3")]
    )

    captured: dict = {}

    async def _fake_complete(messages, system, max_tokens=4096, tools=None):
        captured["message"] = messages[0].content
        return None, {"name": "rank_cited_works", "input": {"matches": []}}

    provider = _FakeProvider(None)
    provider.complete = _fake_complete  # type: ignore[method-assign]

    await _rank(
        db_session, get_settings(), provider, None, chain, [parent_hop], parent_hop.id, None
    )

    assert fetch_referenced_calls == ["W1"]  # ranked from the parent hop's own paper
    assert "Methane flux increases with heat." in captured["message"]  # not chain.origin_text


async def test_rank_rejects_unresolved_parent_hop(db_session) -> None:
    _owner, _project, chain = await _seed_chain(db_session)
    pending_hop = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W1", to_title="Still resolving",
        resolution_status="pending", user_decision="pending",
    )
    db_session.add(pending_hop)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await _rank(
            db_session, get_settings(), _FakeProvider(None), None,
            chain, [pending_hop], pending_hop.id, None,
        )
    assert exc_info.value.status_code == 409


async def test_rank_no_references_never_calls_llm(db_session, monkeypatch) -> None:
    _owner, _project, chain = await _seed_chain(db_session)
    monkeypatch.setattr(synth_backtrace, "fetch_referenced_work_ids", lambda oid: [])
    provider = _FakeProvider(None)

    result = await _rank(db_session, get_settings(), provider, None, chain, [], None, None)

    assert result.candidates == []
    assert result.message
    assert provider.last_messages is None


# ── _commit_hop ──────────────────────────────────────────────────────────────

async def test_commit_hop_abstract_match_creates_root_hop(db_session, monkeypatch) -> None:
    _owner, _project, chain = await _seed_chain(db_session)
    _mock_references(monkeypatch, {"W1": "Methane flux increases sharply with soil temperature."})
    monkeypatch.setattr(
        "athenaeum.api.backtrace.fetch_metadata_by_id",
        lambda oid: _meta(oid, "Methane flux increases sharply with soil temperature."),
    )
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {
                "candidate_index": 0,
                "quote": "Methane flux increases sharply with soil temperature.",
                "relevance_note": "directly supports the claim",
            },
        ]}}
    )

    hop = await _commit_hop(
        db_session, get_settings(), provider, None, chain, [],
        None, "W1", "Author/year match.", _owner.id, None,
    )

    assert hop.parent_hop_id is None
    assert hop.hop_index == 0
    assert hop.resolution_status == "abstract_match"
    assert hop.quote == "Methane flux increases sharply with soil temperature."
    assert hop.match_reasoning == "Author/year match."


async def test_commit_hop_rejects_invalid_openalex_id(db_session, monkeypatch) -> None:
    _owner, _project, chain = await _seed_chain(db_session)
    _mock_references(monkeypatch, {"W1": "abs1"})

    with pytest.raises(HTTPException) as exc_info:
        await _commit_hop(
            db_session, get_settings(), _FakeProvider(None), None, chain, [],
            None, "W-does-not-exist", None, _owner.id, None,
        )
    assert exc_info.value.status_code == 400


async def test_commit_hop_child_gets_incremented_depth_and_parent(db_session, monkeypatch) -> None:
    """The tree-shaping behavior itself: a hop committed under an existing
    hop gets parent_hop_id set and hop_index = parent.hop_index + 1."""
    _owner, _project, chain = await _seed_chain(db_session)

    parent_hop = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W1", to_title="Parent paper", quote="Some verbatim quote.",
        quote_source="abstract", resolution_status="abstract_match", user_decision="pending",
    )
    db_session.add(parent_hop)
    await db_session.flush()

    monkeypatch.setattr(synth_backtrace, "fetch_referenced_work_ids", lambda oid: ["W2"])
    monkeypatch.setattr(
        synth_backtrace, "fetch_works_by_ids", lambda ids: [_meta("W2", "child abstract")]
    )
    monkeypatch.setattr(
        "athenaeum.api.backtrace.fetch_metadata_by_id",
        lambda oid: _meta(oid, "child abstract"),
    )
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 0, "quote": "child abstract", "relevance_note": "supports it"},
        ]}}
    )

    child_hop = await _commit_hop(
        db_session, get_settings(), provider, None, chain, [parent_hop],
        parent_hop.id, "W2", "reasoning", _owner.id, None,
    )

    assert child_hop.parent_hop_id == parent_hop.id
    assert child_hop.hop_index == 1


async def test_commit_hop_two_children_from_same_parent(db_session, monkeypatch) -> None:
    """The whole point of the tree model: two hops can share a parent."""
    _owner, _project, chain = await _seed_chain(db_session)

    parent_hop = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W1", to_title="Parent paper", quote="Some verbatim quote.",
        quote_source="abstract", resolution_status="abstract_match", user_decision="pending",
    )
    db_session.add(parent_hop)
    await db_session.flush()

    monkeypatch.setattr(synth_backtrace, "fetch_referenced_work_ids", lambda oid: ["W2", "W3"])
    monkeypatch.setattr(
        synth_backtrace,
        "fetch_works_by_ids",
        lambda ids: [_meta(i, "abs") for i in ids],
    )
    monkeypatch.setattr(
        "athenaeum.api.backtrace.fetch_metadata_by_id", lambda oid: _meta(oid, "abs")
    )
    provider = _FakeProvider(
        {"name": "select_relevant_items", "input": {"results": [
            {"candidate_index": 0, "quote": "abs", "relevance_note": "supports it"},
        ]}}
    )

    child_a = await _commit_hop(
        db_session, get_settings(), provider, None, chain, [parent_hop],
        parent_hop.id, "W2", "r1", _owner.id, None,
    )
    # Re-load so the second commit's exclusion check sees child_a too.
    _chain, hops_after_a = await _load_chain_and_hops(db_session, chain.id, _owner.id)
    child_b = await _commit_hop(
        db_session, get_settings(), provider, None, chain, hops_after_a,
        parent_hop.id, "W3", "r2", _owner.id, None,
    )

    assert child_a.parent_hop_id == parent_hop.id
    assert child_b.parent_hop_id == parent_hop.id
    assert child_a.hop_index == child_b.hop_index == 1
    assert child_a.to_openalex_id != child_b.to_openalex_id
