"""DB-backed round-trip test for the `.athbacktrace` format — mirrors
test_archive_db_roundtrip.py's pattern for `.ath`: build a real branching
tree, export it, import it into a *different* project (simulating the
actual use case — sharing a trace with someone else's Athenaeum), and
assert the tree's structure survives.

Unlike `.ath`, `.athbacktrace` import is NOT identity-preserving (see
archive/athbacktrace.py's module docstring) — the chain and every hop get
fresh ids, so this compares by relative structure (parent/child
relationships via to_openalex_id, since that's stable across the export)
rather than by literal id equality.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.archive.athbacktrace import build_athbacktrace, import_athbacktrace
from athenaeum.storage.models import (
    BacktraceChain,
    BacktraceHop,
    Paper,
    Project,
    ProjectMember,
    ProjectPaper,
    User,
)


async def _seed_branching_tree(session: AsyncSession) -> tuple[User, Project, BacktraceChain]:
    """origin -> hopA (root) -> hopB (child of A)
                -> hopC (root, sibling of A) — a real two-level branch."""
    owner = User(id=uuid.uuid4(), orcid="0000-0000-0000-0003", display_name="Roundtrip Tester")
    session.add(owner)
    await session.flush()

    project = Project(id=uuid.uuid4(), title="athbacktrace Source Project", owner_id=owner.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))

    origin_paper = Paper(
        id=uuid.uuid4(), openalex_id="W0", doi="10.1234/origin", title="Origin Paper",
        authors_json=[{"name": "Ada Origin"}], venue="Origin Journal", year=2015, status="ghost",
    )
    session.add(origin_paper)
    await session.flush()
    session.add(ProjectPaper(project_id=project.id, paper_id=origin_paper.id, added_by=owner.id))

    chain = BacktraceChain(
        id=uuid.uuid4(), project_id=project.id, created_by=owner.id,
        origin_paper_id=origin_paper.id,
        origin_text="It has long been believed that bacteria are monoploid organisms.",
    )
    session.add(chain)
    await session.flush()

    hop_a = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W1", to_doi="10.1234/hopa", to_title="Hop A Paper",
        to_authors_json=["Smith"], to_year=2010, to_venue="Journal A",
        quote="Verbatim quote A.", quote_source="abstract", relevance_note="supports it",
        match_reasoning="Author/year match.", model="claude-haiku-4-5",
        resolution_status="abstract_match", user_decision="continued",
    )
    session.add(hop_a)
    await session.flush()

    hop_c = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=None, hop_index=0,
        to_openalex_id="W2", to_doi="10.1234/hopc", to_title="Hop C Paper",
        to_authors_json=["Jones"], to_year=2012, to_venue="Journal C",
        quote="Verbatim quote C.", quote_source="abstract", relevance_note="also supports it",
        resolution_status="abstract_match", user_decision="stopped_here",
    )
    session.add(hop_c)
    await session.flush()

    # hop_b escalated to full text and actually resolved a target Paper —
    # exercises the to_paper_resolved re-linking path on import.
    hop_b_target_paper = Paper(
        id=uuid.uuid4(), openalex_id="W3", doi="10.1234/hopb", title="Hop B Target Paper",
        authors_json=[{"name": "Chen"}], venue="Journal B", year=2008, status="ghost",
    )
    session.add(hop_b_target_paper)
    await session.flush()
    session.add(
        ProjectPaper(project_id=project.id, paper_id=hop_b_target_paper.id, added_by=owner.id)
    )

    hop_b = BacktraceHop(
        id=uuid.uuid4(), chain_id=chain.id, parent_hop_id=hop_a.id, hop_index=1,
        to_openalex_id="W3", to_doi="10.1234/hopb", to_title="Hop B Target Paper",
        to_authors_json=["Chen"], to_year=2008, to_venue="Journal B",
        to_paper_id=hop_b_target_paper.id,
        quote="Verbatim quote B.", quote_source="full_text", relevance_note="deep support",
        resolution_status="full_text_match", user_decision="stopped_here",
    )
    session.add(hop_b)
    await session.flush()

    return owner, project, chain


async def test_athbacktrace_survives_export_then_import_into_different_project(db_session) -> None:
    owner, _source_project, chain = await _seed_branching_tree(db_session)

    exported = await build_athbacktrace(db_session, chain.id)

    assert exported["athbacktrace_version"] == 1
    assert exported["chain"]["origin_text"] == chain.origin_text
    assert len(exported["hops"]) == 3

    dest_project = Project(
        id=uuid.uuid4(), title="athbacktrace Destination Project", owner_id=owner.id
    )
    db_session.add(dest_project)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=dest_project.id, user_id=owner.id, role="owner"))
    await db_session.flush()

    new_chain_id = await import_athbacktrace(db_session, exported, dest_project.id, owner.id)
    assert new_chain_id != chain.id  # fresh id, not identity-preserving

    new_chain = await db_session.get(BacktraceChain, new_chain_id)
    assert new_chain is not None
    assert new_chain.project_id == dest_project.id
    assert new_chain.origin_text == chain.origin_text
    assert new_chain.origin_sentence_id is None  # deliberately not carried over

    # The origin paper should have been resolved (by DOI) to a real Paper
    # linked into the destination project.
    origin_link = await db_session.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == dest_project.id,
            ProjectPaper.paper_id == new_chain.origin_paper_id,
        )
    )
    assert origin_link is not None
    origin_paper = await db_session.get(Paper, new_chain.origin_paper_id)
    assert origin_paper is not None
    assert origin_paper.doi == "10.1234/origin"

    new_hops = list(
        await db_session.scalars(
            select(BacktraceHop)
            .where(BacktraceHop.chain_id == new_chain_id)
            .order_by(BacktraceHop.hop_index)
        )
    )
    assert len(new_hops) == 3
    by_openalex = {h.to_openalex_id: h for h in new_hops}
    assert set(by_openalex) == {"W1", "W2", "W3"}

    new_a, new_c, new_b = by_openalex["W1"], by_openalex["W2"], by_openalex["W3"]

    # Structure preserved: A and C are both roots (siblings), B is A's child.
    assert new_a.parent_hop_id is None
    assert new_c.parent_hop_id is None
    assert new_b.parent_hop_id == new_a.id
    assert new_a.hop_index == 0
    assert new_c.hop_index == 0
    assert new_b.hop_index == 1

    # Field-level fidelity, including the fields the roundtrip specifically
    # has to reconstruct rather than copy verbatim.
    assert new_a.quote == "Verbatim quote A."
    assert new_a.match_reasoning == "Author/year match."
    assert new_a.model == "claude-haiku-4-5"
    assert new_a.resolution_status == "abstract_match"
    assert new_c.user_decision == "stopped_here"

    # hop_b's target paper gets re-resolved (by DOI) to a real Paper linked
    # into the destination project, not the exporting DB's literal paper id.
    assert new_b.to_paper_id is not None
    new_target_paper = await db_session.get(Paper, new_b.to_paper_id)
    assert new_target_paper is not None
    assert new_target_paper.doi == "10.1234/hopb"
    target_link = await db_session.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == dest_project.id, ProjectPaper.paper_id == new_b.to_paper_id
        )
    )
    assert target_link is not None


async def test_athbacktrace_reimport_reuses_existing_paper_by_doi(db_session) -> None:
    """Paper.doi is globally unique — the same paper is shared across
    projects via ProjectPaper, never duplicated. Importing the origin
    paper's DOI into a second project must reuse that one global Paper row
    (and add a fresh ProjectPaper link for the new project), not insert a
    second row with the same DOI, which the DB would reject outright."""
    owner, _source_project, chain = await _seed_branching_tree(db_session)
    exported = await build_athbacktrace(db_session, chain.id)

    dest_project = Project(
        id=uuid.uuid4(), title="Dest, Paper Not Yet Linked Here", owner_id=owner.id
    )
    db_session.add(dest_project)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=dest_project.id, user_id=owner.id, role="owner"))
    await db_session.flush()

    new_chain_id = await import_athbacktrace(db_session, exported, dest_project.id, owner.id)
    new_chain = await db_session.get(BacktraceChain, new_chain_id)

    assert new_chain is not None
    assert new_chain.origin_paper_id == chain.origin_paper_id  # same global row, not a duplicate

    all_matching_dois = list(
        await db_session.scalars(select(Paper).where(Paper.doi == "10.1234/origin"))
    )
    assert len(all_matching_dois) == 1  # no duplicate Paper row created

    dest_link = await db_session.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == dest_project.id,
            ProjectPaper.paper_id == chain.origin_paper_id,
        )
    )
    assert dest_link is not None  # but it does get freshly linked into the new project
