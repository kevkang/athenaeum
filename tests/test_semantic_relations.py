"""Semantic relations, step 9b: finding where one paper cites another,
proposing relations from those sentences only, the review rules, and the
endpoints. The model is scripted; everything else is real."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from athenaeum.api import nucleate as api
from athenaeum.domain import nucleate as v
from athenaeum.domain.models import SemanticEdgeCreate, SemanticEdgeUpdate
from athenaeum.nucleate import semantic
from athenaeum.nucleate.corpus import place_node
from athenaeum.nucleate.semantic import (
    InvalidEdgeUpdate,
    apply_edge_update,
    find_mentions,
    first_author_surname,
    propose_relations,
)
from athenaeum.storage.models import (
    NodeReview,
    Nucleus,
    Paper,
    PaperSection,
    PaperSentence,
    Project,
    ProjectMember,
    SemanticEdge,
    SemanticEdgeEvidence,
    User,
)

# ── Finding mentions (pure) ──────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "surname"), [
    ("Benjamin J. Koch", "Koch"),
    ("Koch, Benjamin J.", "Koch"),
    ("Martin Luther King Jr.", "King"),
    ("Marcel van der Heijden", "Heijden"),
    ("B.", None),
])
def test_first_author_surname(name, surname) -> None:
    assert first_author_surname(Paper(title="t", authors_json=[{"name": name}])) == surname


def test_no_authors_means_no_surname() -> None:
    assert first_author_surname(Paper(title="t", authors_json=None)) is None


_SENTENCES = [
    (uuid.uuid4(), "Growth was estimated by qSIP (Koch et al., 2018) in all plots."),
    (uuid.uuid4(), "Earlier work (Koch et al., 2015) used incubations."),
    (uuid.uuid4(), "Following Koch et al. (2018a), we measured 18O uptake."),
    (uuid.uuid4(), "The soils were kochia-dominated grassland in 2018."),
    (uuid.uuid4(), "Rates agree with Müller and Chen (2019)."),
    (uuid.uuid4(), "Young soils grew faster (Smith, 2018)."),
]


def test_mentions_need_the_surname_and_the_year() -> None:
    found = [t for _s, t in find_mentions(_SENTENCES, "Koch", 2018)]
    assert found == [_SENTENCES[0][1], _SENTENCES[2][1]]


def test_accents_are_folded_both_ways() -> None:
    assert find_mentions(_SENTENCES, "Muller", 2019) == [_SENTENCES[4]]


def test_a_surname_that_is_also_a_word_is_ruled_out_by_the_year() -> None:
    """A sentence opening with "Young" names nobody; only the year tells."""
    assert find_mentions(_SENTENCES, "Young", 2021) == []


def test_the_surname_must_be_capitalised() -> None:
    lowercase = [(uuid.uuid4(), "We used the koch 2018 protocol.")]
    assert find_mentions(lowercase, "Koch", 2018) == []


# ── Review rules (pure) ──────────────────────────────────────────────────────


def _edge(**kw) -> SemanticEdge:
    fields = {"relation": "uses_method_of", "proposed_relation": "uses_method_of",
              "provenance": v.AI_PROPOSED} | kw
    return SemanticEdge(id=uuid.uuid4(), project_id=uuid.uuid4(), from_node_id=uuid.uuid4(),
                        to_node_id=uuid.uuid4(), **fields)


def test_accepting_takes_the_proposal_as_is() -> None:
    edge = _edge()
    apply_edge_update(edge, "accept")
    assert (edge.relation, edge.provenance) == ("uses_method_of", v.AI_PROPOSED_ACCEPTED)


def test_correcting_records_the_correction_and_keeps_the_proposal() -> None:
    edge = _edge()
    apply_edge_update(edge, "correct", relation="extends")
    assert (edge.relation, edge.proposed_relation, edge.provenance) == (
        "extends", "uses_method_of", v.AI_PROPOSED_CORRECTED)


def test_correcting_back_to_the_proposal_is_accepting_it() -> None:
    edge = _edge(relation="extends", provenance=v.AI_PROPOSED_CORRECTED)
    apply_edge_update(edge, "correct", relation="uses_method_of")
    assert edge.provenance == v.AI_PROPOSED_ACCEPTED


def test_rejecting_keeps_the_row_and_restoring_undoes_it() -> None:
    edge = _edge()
    apply_edge_update(edge, "reject", rejection_reason="  a passing mention ")
    assert edge.rejected_at is not None and edge.rejection_reason == "a passing mention"
    assert edge.provenance == v.AI_PROPOSED
    apply_edge_update(edge, "restore")
    assert (edge.rejected_at, edge.rejection_reason) == (None, None)


def test_correcting_the_users_own_relation_stays_theirs() -> None:
    edge = _edge(provenance=v.USER_SET, proposed_relation=None)
    apply_edge_update(edge, "correct", relation="extends")
    assert (edge.relation, edge.provenance) == ("extends", v.USER_SET)


@pytest.mark.parametrize(("provenance", "action"), [(v.USER_SET, "accept"),
                                                     (v.USER_SET, "reject")])
def test_the_users_own_relation_is_not_accepted_or_rejected(provenance, action) -> None:
    with pytest.raises(InvalidEdgeUpdate):
        apply_edge_update(_edge(provenance=provenance, proposed_relation=None), action)


def test_an_unknown_relation_is_refused() -> None:
    with pytest.raises(InvalidEdgeUpdate):
        apply_edge_update(_edge(), "correct", relation="cites")


# ── Proposing, against the database ──────────────────────────────────────────


class ScriptedProvider:
    def __init__(self, relations: list[dict] | None = None) -> None:
        self.relations = relations or []
        self.calls: list[str] = []

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.calls.append(messages[0].content)
        return None, {"name": "propose_relations", "input": {"relations": self.relations}}


A_TEXT = [
    "We estimated taxon-specific growth with qSIP following Koch et al. (2018).",
    "Unlike Hungate et al. (2015), we sampled below 1 m.",
    "Soils were collected in 2019.",
]


async def _world(session, a_status: str = "full_text"):
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0009",
                display_name="Relations")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Relations", owner_id=user.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    nucleus = Nucleus(id=uuid.uuid4(), project_id=project.id, question="How fast?")
    a = Paper(id=uuid.uuid4(), title="Deep soil growth", year=2023, status=a_status,
              authors_json=[{"name": "Ana Rivera"}])
    koch = Paper(id=uuid.uuid4(), title="Growth rates by qSIP", year=2018,
                 authors_json=[{"name": "Benjamin J. Koch"}, {"name": "B. Hungate"}])
    hungate = Paper(id=uuid.uuid4(), title="Quantitative SIP", year=2015,
                    authors_json=[{"name": "Bruce A. Hungate"}])
    miller = Paper(id=uuid.uuid4(), title="Unrelated", year=2011,
                   authors_json=[{"name": "Ann Miller"}])
    session.add_all([nucleus, a, koch, hungate, miller])
    await session.flush()
    section = PaperSection(id=uuid.uuid4(), paper_id=a.id, label="Methods", ord=0,
                           char_start=0, char_end=500)
    session.add(section)
    await session.flush()
    for i, text in enumerate(A_TEXT):
        session.add(PaperSentence(id=uuid.uuid4(), paper_id=a.id, section_id=section.id,
                                  ord=i, text=text, char_start=i * 100,
                                  char_end=i * 100 + len(text)))
    nodes = {}
    for paper in (a, koch, hungate, miller):
        nodes[paper.title] = (await place_node(session, nucleus, paper.id)).node
    await session.flush()
    return user, project, nucleus, nodes


def _others(nodes, *titles):
    return [nodes[t] for t in titles]


async def test_proposals_are_read_from_the_sentences_that_cite_each_paper(db_session) -> None:
    user, project, _, nodes = await _world(db_session)
    provider = ScriptedProvider([
        {"paper": 1, "relation": "uses_method_of", "sentence": 1,
         "rationale": "A applies Koch's qSIP method."},
    ])

    result = await propose_relations(
        db_session, provider, project.id, nodes["Deep soil growth"],
        _others(nodes, "Growth rates by qSIP", "Quantitative SIP", "Unrelated"), user.id)

    # Only the citing sentences were shown: Miller, never cited, isn't there.
    (shown,) = provider.calls
    assert "Koch et al. (2018)" in shown and "Hungate et al. (2015)" in shown
    assert "Unrelated" not in shown and "Soils were collected" not in shown

    (edge,) = result.edges
    assert (edge.relation, edge.provenance, edge.proposed_relation) == (
        "uses_method_of", v.AI_PROPOSED, "uses_method_of")
    assert edge.to_node_id == nodes["Growth rates by qSIP"].id
    (evidence,) = (await db_session.scalars(select(SemanticEdgeEvidence).where(
        SemanticEdgeEvidence.edge_id == edge.id))).all()
    assert evidence.verbatim_text == A_TEXT[0]
    assert evidence.text_quote_selector["exact"] == A_TEXT[0]
    assert evidence.source == v.FULL_TEXT

    assert [(o.outcome, o.proposed) for o in result.outcomes] == [
        (semantic.PROPOSED, 1), (semantic.NO_RELATION, 0), (semantic.NO_MENTION, 0)]


async def test_a_relation_cannot_borrow_another_papers_sentence(db_session) -> None:
    """Sentence 2 cites Hungate; it can't be Koch's evidence."""
    user, project, _, nodes = await _world(db_session)
    provider = ScriptedProvider([{"paper": 1, "relation": "extends", "sentence": 2,
                                  "rationale": "x"}])

    result = await propose_relations(
        db_session, provider, project.id, nodes["Deep soil growth"],
        _others(nodes, "Growth rates by qSIP", "Quantitative SIP"), user.id)

    assert result.edges == []


async def test_nothing_cited_means_no_model_call(db_session) -> None:
    user, project, _, nodes = await _world(db_session)
    provider = ScriptedProvider()

    result = await propose_relations(db_session, provider, project.id,
                                     nodes["Deep soil growth"], _others(nodes, "Unrelated"),
                                     user.id)

    assert provider.calls == []
    assert [o.outcome for o in result.outcomes] == [semantic.NO_MENTION]


async def test_without_full_text_nothing_is_read_or_asked(db_session) -> None:
    user, project, _, nodes = await _world(db_session, a_status="ghost")
    provider = ScriptedProvider()

    result = await propose_relations(db_session, provider, project.id,
                                     nodes["Deep soil growth"],
                                     _others(nodes, "Growth rates by qSIP"), user.id)

    assert provider.calls == []
    assert [o.outcome for o in result.outcomes] == [semantic.NO_FULL_TEXT]


async def test_a_rejected_relation_is_not_proposed_again(db_session) -> None:
    user, project, _, nodes = await _world(db_session)
    db_session.add(SemanticEdge(
        id=uuid.uuid4(), project_id=project.id, from_node_id=nodes["Deep soil growth"].id,
        to_node_id=nodes["Growth rates by qSIP"].id, relation="uses_method_of",
        proposed_relation="uses_method_of", provenance=v.AI_PROPOSED,
        rejected_at=datetime.now(UTC)))
    await db_session.flush()
    provider = ScriptedProvider([{"paper": 1, "relation": "uses_method_of", "sentence": 1,
                                  "rationale": "again"}])

    result = await propose_relations(db_session, provider, project.id,
                                     nodes["Deep soil growth"],
                                     _others(nodes, "Growth rates by qSIP"), user.id)

    assert result.edges == []
    assert [o.outcome for o in result.outcomes] == [semantic.ALREADY_RECORDED]


# ── Endpoints ────────────────────────────────────────────────────────────────


async def test_the_canvas_receives_relations_between_its_papers(db_session) -> None:
    user, _, nucleus, nodes = await _world(db_session)
    a, koch = nodes["Deep soil growth"], nodes["Growth rates by qSIP"]

    made = await api.create_semantic_edge(
        nucleus.id, SemanticEdgeCreate(from_node_id=a.id, to_node_id=koch.id,
                                       relation="extends"), db_session, user)
    assert (made.provenance, made.proposed_relation) == (v.USER_SET, None)

    detail = await api.get_nucleus(nucleus.id, db_session, user)
    assert [(e.from_node_id, e.to_node_id, e.relation) for e in detail.semantic_edges] == [
        (a.id, koch.id, "extends")]


async def test_setting_the_same_relation_twice_is_a_conflict(db_session) -> None:
    user, _, nucleus, nodes = await _world(db_session)
    body = SemanticEdgeCreate(from_node_id=nodes["Deep soil growth"].id,
                              to_node_id=nodes["Growth rates by qSIP"].id, relation="extends")
    await api.create_semantic_edge(nucleus.id, body, db_session, user)

    with pytest.raises(HTTPException) as exc:
        await api.create_semantic_edge(nucleus.id, body, db_session, user)
    assert exc.value.status_code == 409


async def test_relations_are_only_between_papers_in_the_nucleus(db_session) -> None:
    user, project, _, nodes = await _world(db_session)
    elsewhere = Nucleus(id=uuid.uuid4(), project_id=project.id, question="Other?")
    db_session.add(elsewhere)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await api.create_semantic_edge(elsewhere.id, SemanticEdgeCreate(
            from_node_id=nodes["Deep soil growth"].id,
            to_node_id=nodes["Growth rates by qSIP"].id, relation="extends"),
            db_session, user)
    assert exc.value.status_code == 404


async def test_review_actions_through_the_api(db_session) -> None:
    user, project, _, nodes = await _world(db_session)
    edge = SemanticEdge(id=uuid.uuid4(), project_id=project.id,
                        from_node_id=nodes["Deep soil growth"].id,
                        to_node_id=nodes["Growth rates by qSIP"].id, relation="supports",
                        proposed_relation="supports", provenance=v.AI_PROPOSED)
    db_session.add(edge)
    await db_session.flush()

    out = await api.update_semantic_edge(
        edge.id, SemanticEdgeUpdate(action="correct", relation="uses_method_of"),
        db_session, user)
    assert (out.relation, out.provenance) == ("uses_method_of", v.AI_PROPOSED_CORRECTED)

    out = await api.update_semantic_edge(
        edge.id, SemanticEdgeUpdate(action="reject", rejection_reason="misread"),
        db_session, user)
    assert out.rejected and out.rejection_reason == "misread"

    # The archivist's relation is rejected, never deleted: that keeps it steering.
    with pytest.raises(HTTPException) as exc:
        await api.delete_semantic_edge(edge.id, db_session, user)
    assert exc.value.status_code == 400


async def test_correcting_into_an_existing_relation_is_a_conflict(db_session) -> None:
    user, project, _, nodes = await _world(db_session)
    pair = {"project_id": project.id, "from_node_id": nodes["Deep soil growth"].id,
            "to_node_id": nodes["Growth rates by qSIP"].id}
    proposed = SemanticEdge(id=uuid.uuid4(), relation="supports", proposed_relation="supports",
                            provenance=v.AI_PROPOSED, **pair)
    mine = SemanticEdge(id=uuid.uuid4(), relation="extends", provenance=v.USER_SET, **pair)
    db_session.add_all([proposed, mine])
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await api.update_semantic_edge(
            proposed.id, SemanticEdgeUpdate(action="correct", relation="extends"),
            db_session, user)
    assert exc.value.status_code == 409


async def test_proposing_through_the_api_leaves_rejected_papers_out(
    db_session, monkeypatch
) -> None:
    user, _, nucleus, nodes = await _world(db_session)
    hungate = nodes["Quantitative SIP"]
    review = await db_session.scalar(select(NodeReview).where(
        NodeReview.nucleus_id == nucleus.id, NodeReview.node_id == hungate.id))
    review.status = v.REJECTED
    await db_session.flush()
    provider = ScriptedProvider([{"paper": 1, "relation": "uses_method_of", "sentence": 1,
                                  "rationale": "qSIP"}])

    async def fake_provider(session, user_id, model, settings):
        return provider

    monkeypatch.setattr(api, "get_provider_for_user", fake_provider)

    out = await api.propose_node_relations(nucleus.id, nodes["Deep soil growth"].id,
                                           db_session, user)

    assert {p.to_node_id for p in out.pairs} == {nodes["Growth rates by qSIP"].id,
                                                 nodes["Unrelated"].id}
    assert "Hungate" not in provider.calls[0]
    (edge,) = out.edges
    assert edge.evidence[0].verbatim_text == A_TEXT[0]
