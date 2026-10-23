"""Tests for the Nucleate data model — the one the handoff says "must be correct
before the first node is written".

Three groups. The worked example the design was approved on (NUCLEATE_PLAN.md,
D-N11), run for real. The guarantees the database itself enforces, each proved
by attempting the illegal write. And the drift guards: vocabulary against CHECK
constraints, and a source scan proving nothing but nucleate/corpus.py writes
lineage or reviews.
"""
from __future__ import annotations

import ast
import pathlib
import re
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from athenaeum.domain import nucleate as vocab
from athenaeum.domain.draft_audit import AUDIT_RUN_KINDS
from athenaeum.nucleate.corpus import (
    RejectedUnderThisNucleus,
    member_node_ids,
    place_node,
    rejected_paper_ids,
)
from athenaeum.storage.models import (
    AuditFinding,
    AuditRun,
    CorpusNode,
    LineageEdge,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusPrompt,
    Paper,
    Project,
    User,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


async def _world(session):
    """A project with the two nuclei and the paper from the worked example."""
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0001",
                display_name="Nucleate Tester")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Soil microbial growth", owner_id=user.id)
    session.add(project)
    await session.flush()

    growth = Nucleus(id=uuid.uuid4(), project_id=project.id,
                     question="How quickly do microbes grow in soil?")
    cue = Nucleus(id=uuid.uuid4(), project_id=project.id,
                  question="What controls microbial carbon use efficiency?")
    koch = Paper(id=uuid.uuid4(), title="Estimating taxon-specific growth rates in soil",
                 doi=f"10.1038/test-{uuid.uuid4().hex[:8]}", year=2018, status="full_text")
    other = Paper(id=uuid.uuid4(), title="Another soil paper", year=2020, status="ghost")
    session.add_all([growth, cue, koch, other])
    await session.flush()
    return project, growth, cue, koch, other


async def _prompt(session, nucleus: Nucleus, text: str = "find growth-rate studies"):
    prompt = NucleusPrompt(id=uuid.uuid4(), nucleus_id=nucleus.id, text=text)
    session.add(prompt)
    await session.flush()
    return prompt


# ── The worked example, for real ─────────────────────────────────────────────


async def test_one_paper_accepted_under_one_nucleus_and_rejected_under_another(
    db_session,
) -> None:
    """D-N11's worked example: Koch 2018 matters to the growth question and is
    rejected for the carbon-use one. Both judgements must hold at once, on one
    shared node — which a single project-wide status could not represent."""
    _, growth, cue, koch, other = await _world(db_session)

    p1 = await _prompt(db_session, growth)
    first = await place_node(db_session, growth, koch.id,
                             prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    first.review.status = vocab.ACCEPTED
    first.review.relevance_text = (
        "Koch et al. use DNA-SIP to estimate taxon-specific growth rates."
    )
    first.review.relevance_provenance = vocab.AI_PROPOSED_ACCEPTED

    # Reached under the second nucleus from another node, via the library.
    p9 = await _prompt(db_session, cue, "carbon use efficiency controls")
    via = await place_node(db_session, cue, other.id,
                           prompt_id=p9.id, retrieval_source=vocab.OPENALEX)
    second = await place_node(db_session, cue, koch.id, from_node_id=via.node.id,
                              prompt_id=p9.id, retrieval_source=vocab.LIBRARY)
    second.review.status = vocab.REJECTED
    second.review.rejection_reason = "about growth rate, not carbon use efficiency"
    await db_session.flush()

    assert first.node.id == second.node.id, "one paper is one node, shared across nuclei"
    reviews = {
        r.nucleus_id: r
        for r in await db_session.scalars(
            select(NodeReview).where(NodeReview.node_id == first.node.id)
        )
    }
    assert reviews[growth.id].status == vocab.ACCEPTED
    assert reviews[cue.id].status == vocab.REJECTED
    assert second.edge.from_kind == vocab.FROM_NODE
    assert second.edge.retrieval_source == vocab.LIBRARY


# ── Placement behaviour ──────────────────────────────────────────────────────


async def test_a_second_path_to_a_node_adds_lineage_but_keeps_the_review(db_session) -> None:
    """Lineage records every path; the user's judgement is never undone by the
    archivist finding the same paper again."""
    _, growth, _, koch, _ = await _world(db_session)
    p1 = await _prompt(db_session, growth)
    first = await place_node(db_session, growth, koch.id,
                             prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    first.review.status = vocab.PINNED
    await db_session.flush()

    p2 = await _prompt(db_session, growth, "more like this")
    again = await place_node(db_session, growth, koch.id,
                             prompt_id=p2.id, retrieval_source=vocab.WEB)

    assert again.new_to_nucleus is False
    assert again.review.id == first.review.id
    assert again.review.status == vocab.PINNED
    edges = list(await db_session.scalars(
        select(LineageEdge).where(LineageEdge.nucleus_id == growth.id)
    ))
    assert len(edges) == 2


async def test_the_archivist_cannot_repropose_a_paper_rejected_here(db_session) -> None:
    _, growth, _, koch, _ = await _world(db_session)
    p1 = await _prompt(db_session, growth)
    placed = await place_node(db_session, growth, koch.id,
                              prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    placed.review.status = vocab.REJECTED
    await db_session.flush()

    assert koch.id in await rejected_paper_ids(db_session, growth.id)
    p2 = await _prompt(db_session, growth, "try again")
    with pytest.raises(RejectedUnderThisNucleus):
        await place_node(db_session, growth, koch.id,
                         prompt_id=p2.id, retrieval_source=vocab.OPENALEX)


async def test_a_rejection_under_one_nucleus_does_not_block_another(db_session) -> None:
    _, growth, cue, koch, _ = await _world(db_session)
    p1 = await _prompt(db_session, growth)
    placed = await place_node(db_session, growth, koch.id,
                              prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    placed.review.status = vocab.REJECTED
    await db_session.flush()

    p2 = await _prompt(db_session, cue)
    elsewhere = await place_node(db_session, cue, koch.id,
                                 prompt_id=p2.id, retrieval_source=vocab.OPENALEX)

    assert elsewhere.new_to_nucleus is True
    assert elsewhere.review.status == vocab.FRONTIER


async def test_a_person_may_readd_a_paper_they_rejected(db_session) -> None:
    """Only the archivist is refused. A person re-adding it is a deliberate choice."""
    _, growth, _, koch, _ = await _world(db_session)
    p1 = await _prompt(db_session, growth)
    placed = await place_node(db_session, growth, koch.id,
                              prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    placed.review.status = vocab.REJECTED
    await db_session.flush()

    readded = await place_node(db_session, growth, koch.id)  # no prompt: added by a person

    assert readded.edge.retrieval_source is None
    assert readded.review.status == vocab.REJECTED, "their earlier judgement is not overwritten"


async def test_prompt_and_source_come_together(db_session) -> None:
    _, growth, _, koch, _ = await _world(db_session)
    p1 = await _prompt(db_session, growth)

    with pytest.raises(ValueError, match="together"):
        await place_node(db_session, growth, koch.id, prompt_id=p1.id)


async def test_membership_is_exactly_the_nodes_with_lineage(db_session) -> None:
    _, growth, cue, koch, other = await _world(db_session)
    p1 = await _prompt(db_session, growth)
    a = await place_node(db_session, growth, koch.id,
                         prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    b = await place_node(db_session, growth, other.id, from_node_id=a.node.id,
                         prompt_id=p1.id, retrieval_source=vocab.OPENALEX)

    assert await member_node_ids(db_session, growth.id) == {a.node.id, b.node.id}
    assert await member_node_ids(db_session, cue.id) == set()


# ── What the database refuses ────────────────────────────────────────────────


async def _review_parts(session):
    _, growth, cue, koch, other = await _world(session)
    p1 = await _prompt(session, growth)
    placed = await place_node(session, growth, koch.id,
                              prompt_id=p1.id, retrieval_source=vocab.OPENALEX)
    return growth, cue, other, placed


async def test_no_review_without_lineage(db_session) -> None:
    """The guarantee that keeps reviews from becoming a membership list: the
    database will not store a review whose introducing edge doesn't exist.

    The review is for a (nucleus, node) pair with no review yet, so the unique
    constraint cannot be what refuses it — only the foreign key can.
    """
    growth, cue, other, _ = await _review_parts(db_session)
    # A real node for `other`, reached under the *other* nucleus only.
    p = await _prompt(db_session, cue)
    elsewhere = await place_node(db_session, cue, other.id,
                                 prompt_id=p.id, retrieval_source=vocab.OPENALEX)

    db_session.add(NodeReview(id=uuid.uuid4(), nucleus_id=growth.id,
                              node_id=elsewhere.node.id, introduced_by_edge_id=uuid.uuid4()))

    with pytest.raises(IntegrityError, match="node_reviews_introduced_by_edge_fk"):
        await db_session.flush()


async def test_a_review_cannot_borrow_an_edge_from_another_nucleus(db_session) -> None:
    """The edge must be in the review's own nucleus and point at its own node —
    the composite key checks both, not merely that some edge exists."""
    _, cue, _, placed = await _review_parts(db_session)

    db_session.add(NodeReview(id=uuid.uuid4(), nucleus_id=cue.id, node_id=placed.node.id,
                              introduced_by_edge_id=placed.edge.id))

    with pytest.raises(IntegrityError, match="node_reviews_introduced_by_edge_fk"):
        await db_session.flush()


async def test_one_node_per_paper_per_project(db_session) -> None:
    growth, _, _, placed = await _review_parts(db_session)
    db_session.add(CorpusNode(id=uuid.uuid4(), project_id=growth.project_id,
                              paper_id=placed.node.paper_id))

    with pytest.raises(IntegrityError, match="corpus_nodes_project_paper_uq"):
        await db_session.flush()


@pytest.mark.parametrize(
    ("mutate", "constraint"),
    [
        (lambda r: setattr(r, "rejection_reason", "not relevant"),
         "node_reviews_rejection_reason_chk"),
        (lambda r: setattr(r, "relevance_text", "unattributed statement"),
         "node_reviews_relevance_pair_chk"),
        (lambda r: setattr(r, "status", "maybe"), "node_reviews_status_chk"),
    ],
    ids=["reason-without-rejection", "statement-without-provenance", "unknown-status"],
)
async def test_review_shapes_the_database_refuses(db_session, mutate, constraint) -> None:
    _, _, _, placed = await _review_parts(db_session)
    mutate(placed.review)

    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


async def test_a_retrieved_edge_must_name_its_source(db_session) -> None:
    growth, _, _, placed = await _review_parts(db_session)
    p = await _prompt(db_session, growth)
    db_session.add(LineageEdge(id=uuid.uuid4(), nucleus_id=growth.id, from_kind="nucleus",
                               to_node_id=placed.node.id, prompt_id=p.id))

    with pytest.raises(IntegrityError, match="lineage_edges_retrieved_or_added_chk"):
        await db_session.flush()


async def test_a_node_edge_cannot_point_at_itself(db_session) -> None:
    growth, _, _, placed = await _review_parts(db_session)
    db_session.add(LineageEdge(id=uuid.uuid4(), nucleus_id=growth.id, from_kind="node",
                               from_node_id=placed.node.id, to_node_id=placed.node.id))

    with pytest.raises(IntegrityError, match="lineage_edges_from_chk"):
        await db_session.flush()


async def test_evidence_cannot_be_stored_unanchored(db_session) -> None:
    """Evidence is verified verbatim before storage, so it is always
    anchorable; the column is NOT NULL, unlike on passages and highlights."""
    _, _, _, placed = await _review_parts(db_session)
    db_session.add(NodeEvidence(id=uuid.uuid4(), review_id=placed.review.id, ord=0,
                                source="abstract", verbatim_text="growth rates spanned",
                                text_quote_selector=None))

    with pytest.raises(IntegrityError, match="text_quote_selector"):
        await db_session.flush()


async def test_deleting_a_nucleus_takes_its_lineage_and_reviews_but_not_the_node(
    db_session,
) -> None:
    """The composite foreign key must not block the cascade, and the node —
    which may belong to other nuclei — survives."""
    growth, cue, _, placed = await _review_parts(db_session)
    p = await _prompt(db_session, cue)
    await place_node(db_session, cue, placed.node.paper_id,
                     prompt_id=p.id, retrieval_source=vocab.WEB)

    # Read the ids now: after expire_all, touching an attribute would trigger a
    # lazy load, which an async session refuses.
    review_id, edge_id, node_id = placed.review.id, placed.edge.id, placed.node.id
    cue_id = cue.id

    await db_session.delete(growth)
    await db_session.flush()
    db_session.expire_all()

    assert await db_session.get(NodeReview, review_id) is None
    assert await db_session.get(LineageEdge, edge_id) is None
    assert await db_session.get(CorpusNode, node_id) is not None
    assert await member_node_ids(db_session, cue_id) == {node_id}


# ── The automatic audit's run record (D-N13) ─────────────────────────────────


async def test_nucleate_audit_run_ties_to_its_expansion_and_finding_to_a_review(
    db_session,
) -> None:
    growth, _, _, placed = await _review_parts(db_session)
    prompt = await _prompt(db_session, growth)
    expansion = NucleusExpansion(id=uuid.uuid4(), nucleus_id=growth.id,
                                 prompt_id=prompt.id, budget=6)
    db_session.add(expansion)
    await db_session.flush()

    run = AuditRun(id=uuid.uuid4(), project_id=growth.project_id, kind="nucleate_node",
                   expansion_id=expansion.id, status="done")
    db_session.add(run)
    await db_session.flush()
    db_session.add(AuditFinding(id=uuid.uuid4(), audit_run_id=run.id, ord=0,
                                node_review_id=placed.review.id, sub_id="1",
                                supported=True, rationale="The quote states it directly."))
    await db_session.flush()


async def test_a_nucleate_audit_run_must_name_its_expansion(db_session) -> None:
    growth, _, _, _ = await _review_parts(db_session)
    db_session.add(AuditRun(id=uuid.uuid4(), project_id=growth.project_id,
                            kind="nucleate_node", status="done"))

    with pytest.raises(IntegrityError, match="audit_runs_kind_payload_chk"):
        await db_session.flush()


# ── Drift guards ─────────────────────────────────────────────────────────────


def _allowed(model, constraint: str) -> set[str]:
    c = next(c for c in model.__table__.constraints if c.name == constraint)
    return set(re.findall(r"'([a-z_]+)'", str(c.sqltext)))


@pytest.mark.parametrize(
    ("model", "constraint", "expected"),
    [
        (NodeReview, "node_reviews_status_chk", set(vocab.REVIEW_STATUSES)),
        (NodeReview, "node_reviews_relevance_provenance_chk", set(vocab.PROVENANCES)),
        (LineageEdge, "lineage_edges_retrieval_source_chk", set(vocab.RETRIEVAL_SOURCES)),
        (LineageEdge, "lineage_edges_from_kind_chk", set(vocab.LINEAGE_FROMS)),
        (NodeEvidence, "node_evidence_source_chk", set(vocab.EVIDENCE_SOURCES)),
        (NucleusExpansion, "nucleus_expansions_status_chk", set(vocab.EXPANSION_STATUSES)),
        (AuditRun, "audit_runs_kind_chk", set(AUDIT_RUN_KINDS)),
    ],
)
def test_database_constraints_allow_exactly_the_vocabulary(model, constraint, expected) -> None:
    assert _allowed(model, constraint) == expected


def test_every_status_and_provenance_has_a_definition() -> None:
    assert set(vocab.REVIEW_STATUS_DEFINITIONS) == set(vocab.REVIEW_STATUSES)
    assert set(vocab.PROVENANCE_DEFINITIONS) == set(vocab.PROVENANCES)
    assert set(vocab.EXPANSION_STATUS_DEFINITIONS) == set(vocab.EXPANSION_STATUSES)
    assert set(vocab.STAGE_LABELS) == set(vocab.STAGES)


def test_evidence_source_reuses_the_backtrace_vocabulary() -> None:
    """The handoff's full_text/abstract are the words BacktraceHop already uses;
    this pins that they stay the same words rather than drifting into a second
    vocabulary."""
    from athenaeum.storage.models import BacktraceHop

    assert _allowed(BacktraceHop, "backtrace_hops_quote_source_chk") == set(vocab.EVIDENCE_SOURCES)


def test_only_the_corpus_module_writes_lineage_or_reviews() -> None:
    """The database stops a review existing without lineage. This stops the
    reverse — lineage written without a review — by making nucleate/corpus.py
    the only place either is ever constructed."""
    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "athenaeum"
    allowed = src / "nucleate" / "corpus.py"
    offenders = []
    for path in src.rglob("*.py"):
        if path == allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"LineageEdge", "NodeReview"}):
                offenders.append(f"{path.relative_to(src)}:{node.lineno}")

    assert not offenders, f"Construct these only via nucleate.corpus.place_node: {offenders}"
