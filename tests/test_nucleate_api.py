"""Tests for the Nucleate API (step 7b) and the rules behind review actions.

The review-update rules are what decide whether a statement renders as AI text
or not, so they're tested directly as pure functions first. Then the endpoints
are driven the way this suite drives others: by calling them with a real
database session, a stub job queue, and no HTTP.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from athenaeum.api import nucleate as api
from athenaeum.domain import nucleate as v
from athenaeum.domain.models import NodeReviewUpdate, NucleusCreate, NucleusPromptCreate
from athenaeum.nucleate.corpus import place_node
from athenaeum.nucleate.review import InvalidReviewUpdate, apply_review_update
from athenaeum.storage.models import (
    AuditFinding,
    AuditRun,
    CorpusNode,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusPrompt,
    Paper,
    Project,
    ProjectMember,
    ProjectPaper,
    User,
)

# ── Review rules (pure) ──────────────────────────────────────────────────────


def _review(**kw) -> NodeReview:
    fields = {"status": v.FRONTIER, "relevance_text": "Koch et al. estimate growth rates.",
              "relevance_provenance": v.AI_PROPOSED, "rejection_reason": None}
    fields.update(kw)
    return NodeReview(id=uuid.uuid4(), nucleus_id=uuid.uuid4(), node_id=uuid.uuid4(),
                      introduced_by_edge_id=uuid.uuid4(), **fields)


def test_accepting_a_node_accepts_its_statement_as_written() -> None:
    """The moment AI text stops being styled as AI text."""
    review = _review()
    apply_review_update(review, status=v.ACCEPTED)
    assert (review.status, review.relevance_provenance) == (v.ACCEPTED, v.AI_PROPOSED_ACCEPTED)


def test_pinning_also_accepts_the_statement() -> None:
    review = _review()
    apply_review_update(review, status=v.PINNED)
    assert review.relevance_provenance == v.AI_PROPOSED_ACCEPTED


def test_editing_an_ai_statement_records_a_correction() -> None:
    review = _review()
    apply_review_update(review, relevance_text="Koch et al. measure growth by DNA-SIP.")
    assert review.relevance_provenance == v.AI_PROPOSED_CORRECTED
    assert review.relevance_text == "Koch et al. measure growth by DNA-SIP."


def test_writing_a_statement_where_there_was_none_is_user_set() -> None:
    review = _review(relevance_text=None, relevance_provenance=None)
    apply_review_update(review, relevance_text="My own description.")
    assert review.relevance_provenance == v.USER_SET


def test_rewriting_your_own_statement_stays_user_set() -> None:
    review = _review(relevance_provenance=v.USER_SET)
    apply_review_update(review, relevance_text="A better description.")
    assert review.relevance_provenance == v.USER_SET


def test_resubmitting_the_same_text_is_not_an_edit() -> None:
    review = _review()
    apply_review_update(review, relevance_text="  Koch et al. estimate growth rates.  ")
    assert review.relevance_provenance == v.AI_PROPOSED


def test_rejecting_leaves_the_statement_provenance_alone() -> None:
    """Rejecting a paper is not a judgement on how it was described."""
    review = _review()
    apply_review_update(review, status=v.REJECTED, rejection_reason="wrong ecosystem")
    assert review.relevance_provenance == v.AI_PROPOSED
    assert review.rejection_reason == "wrong ecosystem"


def test_moving_off_rejected_clears_the_reason() -> None:
    review = _review(status=v.REJECTED, rejection_reason="wrong ecosystem")
    apply_review_update(review, status=v.FRONTIER)
    assert review.rejection_reason is None


def test_a_reason_without_a_rejection_is_refused() -> None:
    with pytest.raises(InvalidReviewUpdate, match="rejection"):
        apply_review_update(_review(), status=v.ACCEPTED, rejection_reason="why not")


def test_clearing_the_statement_clears_its_provenance() -> None:
    review = _review()
    apply_review_update(review, relevance_text="   ")
    assert (review.relevance_text, review.relevance_provenance) == (None, None)


@pytest.mark.parametrize(
    ("status", "has_abstract", "expected"),
    [("full_text", False, "available"), ("full_text", True, "available"),
     ("ghost", True, "abstract_only"), ("ghost", False, "user_must_supply"),
     ("failed", True, "user_must_supply")],
)
def test_fulltext_state_is_derived_from_paper_status_and_abstract(
    status, has_abstract, expected
) -> None:
    assert v.fulltext_state(status, has_abstract) == expected


@pytest.mark.parametrize(
    ("event", "terminal"),
    [({"stage": "awaiting_review", "status": "completed"}, True),
     ({"stage": "ranking", "status": "failed"}, True),
     ({"stage": "placing", "status": "completed"}, False),
     # A node's own status never ends the stream, even one that sounds final.
     ({"stage": "placing", "status": "ready", "node_id": "n"}, False),
     ({"stage": "ingesting", "status": "failed", "node_id": "n"}, False)],
)
def test_which_events_end_the_stream(event, terminal) -> None:
    assert api.is_terminal_event(event) is terminal


# ── Endpoints ────────────────────────────────────────────────────────────────


class _Queue:
    """A stand-in for the arq pool that records what was enqueued."""

    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[tuple] = []
        self.fail = fail

    async def enqueue_job(self, *args) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.jobs.append(args)


def _request(queue: _Queue):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(arq_pool=queue)))


async def _member(session) -> tuple[User, Project]:
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0003",
                display_name="API Tester")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="API project", owner_id=user.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    await session.flush()
    return user, project


async def test_creating_a_nucleus_starts_its_first_expansion(db_session) -> None:
    user, project = await _member(db_session)
    queue = _Queue()

    detail = await api.create_nucleus(
        project.id, NucleusCreate(question="  How quickly do microbes grow in soil?  "),
        db_session, user, _request(queue))

    assert detail.nucleus.question == "How quickly do microbes grow in soil?"
    (first,) = detail.prompts
    assert first.text == detail.nucleus.question, "the question is also the first prompt"
    assert first.expansion is not None and first.expansion.status == v.RUNNING
    assert first.expansion.budget == v.DEFAULT_EXPANSION_BUDGET
    assert queue.jobs == [("run_expansion_job", str(first.expansion.id), str(user.id))]


async def test_an_unreachable_queue_fails_the_run_visibly(db_session) -> None:
    """Never left 'running' with nothing working on it."""
    user, project = await _member(db_session)

    with pytest.raises(HTTPException) as exc:
        await api.create_nucleus(project.id, NucleusCreate(question="q?"), db_session, user,
                                 _request(_Queue(fail=True)))

    assert exc.value.status_code == 503
    nucleus = (await api.list_nuclei(project.id, db_session, user))[0]
    detail = await api.get_nucleus(nucleus.id, db_session, user)
    assert detail.prompts[0].expansion.status == v.FAILED


async def test_non_members_are_refused(db_session) -> None:
    _, project = await _member(db_session)
    outsider = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0004",
                    display_name="Outsider")
    db_session.add(outsider)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await api.create_nucleus(project.id, NucleusCreate(question="q?"), db_session,
                                 outsider, _request(_Queue()))
    assert exc.value.status_code == 403


async def test_a_second_prompt_waits_for_the_first_to_finish(db_session) -> None:
    user, project = await _member(db_session)
    detail = await api.create_nucleus(project.id, NucleusCreate(question="q?"), db_session,
                                      user, _request(_Queue()))

    with pytest.raises(HTTPException) as exc:
        await api.send_prompt(detail.nucleus.id, NucleusPromptCreate(text="more"), db_session,
                              user, _request(_Queue()))
    assert exc.value.status_code == 409


async def _finished_nucleus(session, user, project) -> Nucleus:
    nucleus = Nucleus(id=uuid.uuid4(), project_id=project.id, question="q?")
    session.add(nucleus)
    await session.flush()
    return nucleus


async def test_anchors_must_be_nodes_in_this_nucleus(db_session) -> None:
    user, project = await _member(db_session)
    nucleus = await _finished_nucleus(db_session, user, project)

    with pytest.raises(HTTPException) as exc:
        await api.send_prompt(nucleus.id, NucleusPromptCreate(
            text="expand", anchor_node_ids=[uuid.uuid4()]), db_session, user, _request(_Queue()))
    assert exc.value.status_code == 400


async def test_cancel_stops_a_running_expansion_once(db_session) -> None:
    user, project = await _member(db_session)
    detail = await api.create_nucleus(project.id, NucleusCreate(question="q?"), db_session,
                                      user, _request(_Queue()))
    expansion_id = detail.prompts[0].expansion.id

    cancelled = await api.cancel_expansion(expansion_id, db_session, user)
    assert cancelled.status == v.CANCELLED
    with pytest.raises(HTTPException) as exc:
        await api.cancel_expansion(expansion_id, db_session, user)
    assert exc.value.status_code == 409


# ── The canvas ───────────────────────────────────────────────────────────────


async def test_the_canvas_shows_each_node_with_its_evidence_audit_and_other_nuclei(
    db_session,
) -> None:
    user, project = await _member(db_session)
    here = await _finished_nucleus(db_session, user, project)
    elsewhere = await _finished_nucleus(db_session, user, project)
    prompt = NucleusPrompt(id=uuid.uuid4(), nucleus_id=here.id, text="q?")
    db_session.add(prompt)
    await db_session.flush()
    expansion = NucleusExpansion(id=uuid.uuid4(), nucleus_id=here.id, prompt_id=prompt.id,
                                 budget=6, status=v.DONE, finished_at=datetime.now(UTC))
    abstract = "Growth rates spanned two orders of magnitude."
    paper = Paper(id=uuid.uuid4(), title="Koch", status="ghost", abstract=abstract,
                  authors_json=[{"name": "A. Koch"}], year=2018,
                  doi=f"10.9999/{uuid.uuid4().hex[:8]}")
    db_session.add_all([expansion, paper])
    await db_session.flush()

    placed = await place_node(db_session, here, paper.id, prompt_id=prompt.id,
                              retrieval_source=v.OPENALEX)
    placed.review.relevance_text = "Koch et al. report growth rates."
    placed.review.relevance_provenance = v.AI_PROPOSED
    db_session.add(NodeEvidence(
        id=uuid.uuid4(), review_id=placed.review.id, ord=0, source=v.ABSTRACT,
        verbatim_text="two orders of magnitude",
        text_quote_selector={"type": "TextQuoteSelector", "exact": "two orders of magnitude",
                             "prefix": "Growth rates spanned ", "suffix": "."}))
    run = AuditRun(id=uuid.uuid4(), project_id=project.id, kind="nucleate_node",
                   expansion_id=expansion.id, status="done")
    db_session.add(run)
    await db_session.flush()
    db_session.add(AuditFinding(id=uuid.uuid4(), audit_run_id=run.id, ord=0,
                                node_review_id=placed.review.id, sub_id="1", supported=True,
                                rationale="Stated directly."))
    await place_node(db_session, elsewhere, paper.id)  # the same paper, another question
    await db_session.flush()

    detail = await api.get_nucleus(here.id, db_session, user)

    (node,) = detail.nodes
    assert node.paper.authors == ["A. Koch"]
    assert node.fulltext == v.FULLTEXT_ABSTRACT_ONLY
    assert node.review.relevance_provenance == v.AI_PROPOSED
    assert [e.source for e in node.evidence] == [v.ABSTRACT]
    assert node.audit is not None and node.audit.supported is True
    assert node.other_nucleus_ids == [elsewhere.id], "cross-nucleus nodes are marked"
    (edge,) = detail.lineage
    assert (edge.from_kind, edge.retrieval_source) == (v.FROM_NUCLEUS, v.OPENALEX)
    assert detail.prompts[0].expansion.status == v.DONE


async def test_the_drawer_actions_persist_through_the_endpoint(db_session) -> None:
    user, project = await _member(db_session)
    nucleus = await _finished_nucleus(db_session, user, project)
    paper = Paper(id=uuid.uuid4(), title="Koch", status="ghost")
    db_session.add(paper)
    await db_session.flush()
    placed = await place_node(db_session, nucleus, paper.id)
    placed.review.relevance_text = "An AI statement."
    placed.review.relevance_provenance = v.AI_PROPOSED
    await db_session.flush()

    out = await api.update_review(placed.review.id, NodeReviewUpdate(status="accepted"),
                                  db_session, user)
    assert out.relevance_provenance == v.AI_PROPOSED_ACCEPTED

    with pytest.raises(HTTPException) as exc:
        await api.update_review(placed.review.id,
                                NodeReviewUpdate(rejection_reason="but it's accepted"),
                                db_session, user)
    assert exc.value.status_code == 400



# ── D-N18: keeping a paper puts it in the library ────────────────────────────


async def _review_for_new_paper(session, user, project) -> NodeReview:
    nucleus = await _finished_nucleus(session, user, project)
    paper = Paper(id=uuid.uuid4(), title="Kept?", status="ghost")
    session.add(paper)
    await session.flush()
    return (await place_node(session, nucleus, paper.id)).review


async def _linked(session, project, review) -> bool:
    """Whether the review's paper is in the project's library."""
    node = await session.get(CorpusNode, review.node_id)
    return await session.get(ProjectPaper, (project.id, node.paper_id)) is not None


@pytest.mark.parametrize("status", ["accepted", "pinned"])
async def test_keeping_a_node_adds_its_paper_to_the_library(db_session, status) -> None:
    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)

    await api.update_review(review.id, NodeReviewUpdate(status=status), db_session, user)
    await api.update_review(review.id, NodeReviewUpdate(status=status), db_session, user)

    assert await _linked(db_session, project, review), "idempotent, and linked once"


@pytest.mark.parametrize("status", ["frontier", "rejected"])
async def test_frontier_and_rejected_papers_stay_out_of_the_library(db_session, status) -> None:
    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)

    await api.update_review(review.id, NodeReviewUpdate(status=status), db_session, user)

    assert not await _linked(db_session, project, review)


async def test_rejecting_a_kept_paper_leaves_it_in_the_library(db_session) -> None:
    """It may already carry highlights."""
    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)

    await api.update_review(review.id, NodeReviewUpdate(status="accepted"), db_session, user)
    await api.update_review(review.id, NodeReviewUpdate(status="rejected"), db_session, user)

    assert await _linked(db_session, project, review)


# ── A corpus paper is visible to the project's members ───────────────────────


async def test_a_corpus_paper_is_visible_to_members_and_nobody_else(db_session) -> None:
    """A needs-full-text node must accept a supplied PDF, and its paper is in
    the corpus but not the library. Visibility follows corpus membership —
    and only for members."""
    from athenaeum.api.papers import _assert_paper_visible

    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)
    node = await db_session.get(CorpusNode, review.node_id)
    assert not await _linked(db_session, project, review), "in the corpus, not the library"

    await _assert_paper_visible(db_session, node.paper_id, user.id)  # no exception

    outsider = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0006",
                    display_name="Outsider")
    db_session.add(outsider)
    await db_session.flush()
    with pytest.raises(HTTPException) as exc:
        await _assert_paper_visible(db_session, node.paper_id, outsider.id)
    assert exc.value.status_code == 403


# ── Opening in Read, supplying full text (D-N23) ─────────────────────────────


async def test_opening_a_paper_in_read_puts_it_in_the_library(db_session) -> None:
    """The drawer's "Open in Read" and its upload both link the paper; neither
    accepts it on the user's behalf."""
    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)
    assert not await _linked(db_session, project, review)

    await api.add_node_to_library(review.nucleus_id, review.node_id, db_session, user)

    assert await _linked(db_session, project, review)
    await db_session.refresh(review)
    assert review.status == v.FRONTIER


async def test_only_a_node_in_this_nucleus_can_be_added(db_session) -> None:
    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)
    other = await _finished_nucleus(db_session, user, project)

    with pytest.raises(HTTPException) as exc:
        await api.add_node_to_library(other.id, review.node_id, db_session, user)
    assert exc.value.status_code == 404


async def test_a_zotero_item_can_supply_an_existing_paper(db_session) -> None:
    from athenaeum.api.zotero import supply_existing_paper

    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)
    node = await db_session.get(CorpusNode, review.node_id)

    paper = await supply_existing_paper(db_session, node.paper_id, "ZKEY0001", "sync", user.id)

    assert paper.id == node.paper_id, "filled in, not a second paper"
    assert (paper.zotero_item_key, paper.zotero_source) == ("ZKEY0001", "sync")


async def test_a_zotero_item_already_another_paper_is_refused(db_session) -> None:
    from athenaeum.api.zotero import supply_existing_paper

    user, project = await _member(db_session)
    review = await _review_for_new_paper(db_session, user, project)
    node = await db_session.get(CorpusNode, review.node_id)
    db_session.add(Paper(id=uuid.uuid4(), title="Imported before", zotero_item_key="ZKEY0002"))
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await supply_existing_paper(db_session, node.paper_id, "ZKEY0002", "sync", user.id)
    assert exc.value.status_code == 409


async def test_supplying_someone_elses_paper_is_refused(db_session) -> None:
    from athenaeum.api.zotero import supply_existing_paper

    user, _ = await _member(db_session)
    stranger_paper = Paper(id=uuid.uuid4(), title="Not in any of your projects")
    db_session.add(stranger_paper)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await supply_existing_paper(db_session, stranger_paper.id, "ZKEY0003", "sync", user.id)
    assert exc.value.status_code == 403
