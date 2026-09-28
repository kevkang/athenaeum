"""The semantic-edge schema (step 9a): what the database refuses, the drift
guards tying it to domain/relations.py, and migration 0035's carry-over of
legacy KnowledgeLink rows."""
from __future__ import annotations

import importlib.util
import pathlib
import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from athenaeum.domain import nucleate as vocab
from athenaeum.domain.relations import SEMANTIC_RELATIONS
from athenaeum.storage.models import (
    CorpusNode,
    KnowledgeLink,
    Paper,
    Project,
    SemanticEdge,
    SemanticEdgeEvidence,
    User,
)

_MIGRATION = (pathlib.Path(__file__).resolve().parent.parent
              / "migrations" / "versions" / "0035_semantic_edges.py")


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0035", _MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _world(session):
    """A user, two projects, and two corpus papers in the first."""
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0002",
                display_name="Relations Tester")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Soil", owner_id=user.id)
    elsewhere = Project(id=uuid.uuid4(), title="Elsewhere", owner_id=user.id)
    koch = Paper(id=uuid.uuid4(), title="Koch 2018", year=2018)
    hungate = Paper(id=uuid.uuid4(), title="Hungate 2015", year=2015)
    session.add_all([project, elsewhere, koch, hungate])
    await session.flush()
    a = CorpusNode(id=uuid.uuid4(), project_id=project.id, paper_id=koch.id)
    b = CorpusNode(id=uuid.uuid4(), project_id=project.id, paper_id=hungate.id)
    foreign = CorpusNode(id=uuid.uuid4(), project_id=elsewhere.id, paper_id=hungate.id)
    session.add_all([a, b, foreign])
    await session.flush()
    return user, project, a, b, foreign


def _edge(project, a, b, **kw) -> SemanticEdge:
    fields = {"relation": "uses_method_of", "provenance": "ai_proposed",
              "proposed_relation": "uses_method_of"} | kw
    return SemanticEdge(id=uuid.uuid4(), project_id=project.id,
                        from_node_id=a.id, to_node_id=b.id, **fields)


# ── What is allowed ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("provenance", "relation", "proposed"), [
    ("ai_proposed", "extends", "extends"),
    ("ai_proposed_accepted", "extends", "extends"),
    ("ai_proposed_corrected", "supports", "extends"),
    ("user_set", "disagrees_with", None),
])
async def test_each_provenance_with_a_matching_proposal_is_stored(
    db_session, provenance, relation, proposed
) -> None:
    _, project, a, b, _ = await _world(db_session)
    db_session.add(_edge(project, a, b, provenance=provenance, relation=relation,
                         proposed_relation=proposed))
    await db_session.flush()


async def test_one_pair_can_carry_several_relations(db_session) -> None:
    """A paper can both use B's method and use B's data."""
    _, project, a, b, _ = await _world(db_session)
    db_session.add_all([
        _edge(project, a, b),
        _edge(project, a, b, relation="uses_data_from", proposed_relation="uses_data_from"),
    ])
    await db_session.flush()


# ── What the database refuses ────────────────────────────────────────────────


@pytest.mark.parametrize(("fields", "constraint"), [
    ({"relation": "cites", "provenance": "user_set", "proposed_relation": None},
     "semantic_edges_relation_chk"),
    ({"relation": "uses_method_of", "provenance": "user_set", "proposed_relation": "cites"},
     "semantic_edges_proposed_relation_chk"),
    # An unknown provenance breaks both provenance rules, and Postgres reports
    # whichever it checks first; the drift guard below pins the vocabulary.
    ({"provenance": "guessed"}, "semantic_edges_provenance(_agrees)?_chk"),
    # Accepted means accepted as proposed.
    ({"provenance": "ai_proposed_accepted", "relation": "extends"},
     "semantic_edges_provenance_agrees_chk"),
    # A "correction" to the same relation is not a correction.
    ({"provenance": "ai_proposed_corrected"}, "semantic_edges_provenance_agrees_chk"),
    # The user's own relation has no proposal behind it.
    ({"provenance": "user_set"}, "semantic_edges_provenance_agrees_chk"),
    # An AI relation always records what was proposed. (With `=` rather than
    # IS NOT DISTINCT FROM, this compared as NULL and the CHECK let it through.)
    ({"proposed_relation": None}, "semantic_edges_provenance_agrees_chk"),
    ({"provenance": "ai_proposed_accepted", "proposed_relation": None},
     "semantic_edges_provenance_agrees_chk"),
    ({"rejection_reason": "not what it says"}, "semantic_edges_rejection_reason_chk"),
])
async def test_the_database_refuses(db_session, fields, constraint) -> None:
    _, project, a, b, _ = await _world(db_session)
    db_session.add(_edge(project, a, b, **fields))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


async def test_a_paper_cannot_relate_to_itself(db_session) -> None:
    _, project, a, _, _ = await _world(db_session)
    db_session.add(_edge(project, a, a))
    with pytest.raises(IntegrityError, match="semantic_edges_not_self_chk"):
        await db_session.flush()


async def test_both_papers_must_be_in_the_edges_project(db_session) -> None:
    _, project, a, _, foreign = await _world(db_session)
    db_session.add(_edge(project, a, foreign))
    with pytest.raises(IntegrityError, match="semantic_edges_to_node_fk"):
        await db_session.flush()


async def test_the_same_relation_between_the_same_pair_is_stored_once(db_session) -> None:
    _, project, a, b, _ = await _world(db_session)
    db_session.add(_edge(project, a, b))
    await db_session.flush()
    db_session.add(_edge(project, a, b))
    with pytest.raises(IntegrityError, match="semantic_edges_pair_relation_uq"):
        await db_session.flush()


async def test_a_rejected_edge_keeps_its_reason(db_session) -> None:
    _, project, a, b, _ = await _world(db_session)
    db_session.add(_edge(project, a, b, rejected_at=datetime.now(UTC),
                         rejection_reason="B is only mentioned in passing"))
    await db_session.flush()


@pytest.mark.parametrize("selector", [
    {"type": "TextQuoteSelector"},                  # no exact
    {"type": "TextPositionSelector", "exact": "x"},  # wrong kind
])
async def test_edge_evidence_must_be_anchored(db_session, selector) -> None:
    _, project, a, b, _ = await _world(db_session)
    edge = _edge(project, a, b)
    db_session.add(edge)
    await db_session.flush()
    db_session.add(SemanticEdgeEvidence(id=uuid.uuid4(), edge_id=edge.id, ord=0,
                                        source="full_text", verbatim_text="x",
                                        text_quote_selector=selector))
    with pytest.raises(IntegrityError, match="semantic_edge_evidence_text_quote_selector_chk"):
        await db_session.flush()


# ── Drift guards ─────────────────────────────────────────────────────────────


def _allowed(constraint: str) -> set[str]:
    c = next(c for c in SemanticEdge.__table__.constraints if c.name == constraint)
    return set(re.findall(r"'([a-z_]+)'", str(c.sqltext)))


def test_the_database_allows_exactly_the_six_relations() -> None:
    assert _allowed("semantic_edges_relation_chk") == set(SEMANTIC_RELATIONS)
    assert _allowed("semantic_edges_proposed_relation_chk") == set(SEMANTIC_RELATIONS)


def test_edges_share_the_statement_provenance_vocabulary() -> None:
    """D-N12: one provenance vocabulary for statements and edges."""
    assert _allowed("semantic_edges_provenance_chk") == set(vocab.PROVENANCES)


def test_the_migrations_frozen_relations_match_the_live_ones() -> None:
    assert set(_migration()._RELATIONS) == set(SEMANTIC_RELATIONS)


# ── Migration 0035's KnowledgeLink carry-over ────────────────────────────────


async def test_legacy_links_carry_over_only_where_a_relation_maps(db_session) -> None:
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0003",
                display_name="Legacy")
    db_session.add(user)
    await db_session.flush()
    project = Project(id=uuid.uuid4(), title="Legacy links", owner_id=user.id)
    p1, p2, p3 = (Paper(id=uuid.uuid4(), title=f"Legacy {i}") for i in range(3))
    db_session.add_all([project, p1, p2, p3])
    await db_session.flush()

    def link(a, b, rel, from_type="paper", to_type="paper"):
        return KnowledgeLink(id=uuid.uuid4(), project_id=project.id, from_type=from_type,
                             from_id=a, to_type=to_type, to_id=b, rel=rel, created_by=user.id)

    db_session.add_all([
        link(p1.id, p2.id, "contradicts"),   # → disagrees_with
        link(p2.id, p3.id, "extends"),       # → extends
        link(p1.id, p3.id, "cites"),         # no equivalent
        link(p1.id, p2.id, "related"),       # no equivalent
        link(p1.id, uuid.uuid4(), "supports", to_type="synthesis"),  # no paper at one end
    ])
    await db_session.flush()

    migration = _migration()
    await db_session.run_sync(lambda s: migration._carry_over_knowledge_links(s.connection()))

    nodes = {n.paper_id: n.id for n in (await db_session.scalars(
        select(CorpusNode).where(CorpusNode.project_id == project.id))).all()}
    edges = (await db_session.scalars(
        select(SemanticEdge).where(SemanticEdge.project_id == project.id))).all()
    assert {(e.from_node_id, e.to_node_id, e.relation) for e in edges} == {
        (nodes[p1.id], nodes[p2.id], "disagrees_with"),
        (nodes[p2.id], nodes[p3.id], "extends"),
    }
    # The user drew them, so they are the user's, with no proposal behind them.
    assert {(e.provenance, e.proposed_relation, e.created_by) for e in edges} == {
        ("user_set", None, user.id)
    }
    # knowledge_links is left as it was.
    assert len((await db_session.scalars(
        select(KnowledgeLink).where(KnowledgeLink.project_id == project.id))).all()) == 5
