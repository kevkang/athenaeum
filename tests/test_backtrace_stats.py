"""Tests for Backtrace resolution coverage (domain/backtrace_stats.py) and
the two endpoints over it.

Most of these pin down *definitions* rather than arithmetic, because the
definitions are the part that can quietly make a published number dishonest:
which statuses count as a failure, which are excluded from the denominator
entirely, and the difference between "no rate" and "a rate of zero".
"""
from __future__ import annotations

import itertools
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.backtrace import get_chain_resolution
from athenaeum.api.public import get_backtrace_resolution
from athenaeum.domain import backtrace_stats as bt_stats
from athenaeum.storage.models import (
    BacktraceChain,
    BacktraceHop,
    Paper,
    Project,
    ProjectMember,
    User,
)

# Several of these tests need two projects at once (private vs public, or two
# chains), so each seeded owner needs its own ORCID — the column is unique.
# The 9999 block keeps these clear of the fixed ORCIDs other test modules use.
_orcid_counter = itertools.count(1)


async def _seed_chain(
    session: AsyncSession, *, is_public: bool = False
) -> tuple[User, Project, BacktraceChain]:
    owner = User(
        id=uuid.uuid4(),
        orcid=f"0000-0000-9999-{next(_orcid_counter):04d}",
        display_name="Coverage Tester",
    )
    session.add(owner)
    await session.flush()

    project = Project(
        id=uuid.uuid4(),
        title="Backtrace Coverage Test Project",
        owner_id=owner.id,
        is_public=is_public,
    )
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))

    origin_paper = Paper(
        id=uuid.uuid4(), openalex_id=f"W{uuid.uuid4().hex[:10]}",
        title="Origin Paper", status="ghost",
    )
    session.add(origin_paper)
    await session.flush()

    chain = BacktraceChain(
        id=uuid.uuid4(), project_id=project.id, created_by=owner.id,
        origin_paper_id=origin_paper.id,
        origin_text="Warming increases soil CO2 efflux.",
    )
    session.add(chain)
    await session.flush()
    return owner, project, chain


async def _add_hops(session, chain, specs: list[tuple[int, str, int]]) -> None:
    """specs: (hop_index, resolution_status, how_many). `to_openalex_id` is
    made unique per hop to satisfy backtrace_hops_chain_target_uq."""
    for hop_index, status, count in specs:
        for _ in range(count):
            session.add(
                BacktraceHop(
                    id=uuid.uuid4(),
                    chain_id=chain.id,
                    parent_hop_id=None,
                    hop_index=hop_index,
                    to_openalex_id=f"W{uuid.uuid4().hex[:12]}",
                    to_title="Cited work",
                    resolution_status=status,
                    user_decision="pending",
                )
            )
    await session.flush()


# ── Definitions ──────────────────────────────────────────────────────────────

async def test_no_hops_yields_no_rate_rather_than_zero(db_session) -> None:
    """An unmeasured rate and a measured rate of zero are different claims;
    the UI has to be able to tell them apart, so this must be None."""
    _owner, _project, chain = await _seed_chain(db_session)

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain.id)

    assert coverage.attempted == 0
    assert coverage.resolution_rate is None
    assert coverage.by_depth == ()
    assert coverage.deepest is None


async def test_awaiting_manual_import_counts_as_unresolved_not_in_flight(db_session) -> None:
    """The automatic path has given up and handed the work to a human, which
    may never happen. Counting it as in-flight would hide a failure in a
    bucket that never resolves, inflating the rate."""
    _owner, _project, chain = await _seed_chain(db_session)
    await _add_hops(db_session, chain, [(0, "abstract_match", 1), (0, "awaiting_manual_import", 1)])

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain.id)

    assert coverage.unresolved == 1
    assert coverage.in_flight == 0
    assert coverage.attempted == 2
    assert coverage.resolution_rate == pytest.approx(0.5)


async def test_in_flight_hops_are_excluded_from_the_denominator(db_session) -> None:
    """A hop that hasn't finished looking is not yet evidence of anything."""
    _owner, _project, chain = await _seed_chain(db_session)
    await _add_hops(
        db_session, chain,
        [(0, "full_text_match", 1), (0, "pending", 3), (0, "ingesting", 2)],
    )

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain.id)

    assert coverage.in_flight == 5
    assert coverage.attempted == 1
    assert coverage.resolution_rate == pytest.approx(1.0)


async def test_both_match_kinds_count_as_resolved(db_session) -> None:
    _owner, _project, chain = await _seed_chain(db_session)
    await _add_hops(db_session, chain, [(0, "abstract_match", 2), (0, "full_text_match", 3)])

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain.id)

    assert coverage.resolved == 5
    assert coverage.resolution_rate == pytest.approx(1.0)


# ── Depth breakdown ──────────────────────────────────────────────────────────

async def test_depth_is_one_based_and_decays_are_visible_per_level(db_session) -> None:
    """hop_index 0 is one hop back from the origin, so it reads as depth 1 —
    matching BacktraceChainSummaryOut.max_depth's existing convention."""
    _owner, _project, chain = await _seed_chain(db_session)
    await _add_hops(
        db_session, chain,
        [
            (0, "abstract_match", 8), (0, "no_match", 6),        # depth 1: 8/14
            (1, "full_text_match", 3), (1, "no_match", 19),      # depth 2: 3/22
        ],
    )

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain.id)

    assert [d.depth for d in coverage.by_depth] == [1, 2]
    assert coverage.by_depth[0].attempted == 14
    assert coverage.by_depth[0].resolution_rate == pytest.approx(8 / 14)
    assert coverage.by_depth[1].resolution_rate == pytest.approx(3 / 22)
    assert coverage.deepest_depth == 2
    # The headline rate hides the decay the depth rail exists to show.
    assert coverage.resolution_rate == pytest.approx(11 / 36)


async def test_deepest_skips_levels_with_nothing_finished(db_session) -> None:
    """A level that is entirely still running has no rate to report, so it
    must not be presented as the tip's measured rate."""
    _owner, _project, chain = await _seed_chain(db_session)
    await _add_hops(
        db_session, chain,
        [(0, "abstract_match", 2), (1, "no_match", 1), (2, "pending", 4)],
    )

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain.id)

    assert coverage.deepest_depth == 3        # the tree does reach depth 3
    assert coverage.deepest is not None
    assert coverage.deepest.depth == 2        # but depth 2 is the deepest measured
    assert coverage.deepest.resolution_rate == pytest.approx(0.0)


# ── Scoping ──────────────────────────────────────────────────────────────────

async def test_chain_scope_excludes_other_chains(db_session) -> None:
    _owner, _project, chain_a = await _seed_chain(db_session)
    _owner_b, _project_b, chain_b = await _seed_chain(db_session)
    await _add_hops(db_session, chain_a, [(0, "abstract_match", 1)])
    await _add_hops(db_session, chain_b, [(0, "no_match", 5)])

    coverage = await bt_stats.load_coverage(db_session, chain_id=chain_a.id)

    assert coverage.chain_count == 1
    assert coverage.resolved == 1
    assert coverage.unresolved == 0


async def test_public_aggregate_counts_only_public_projects(db_session) -> None:
    """The unauthenticated endpoint must never leak counts from private
    work — a private project's failures shouldn't move the public number
    either way. The database may already hold other public trees, so this
    compares before and after seeding; every count is a sum, so the
    difference is exact."""
    before = await bt_stats.load_coverage(db_session, public_only=True)

    _owner, public_project, public_chain = await _seed_chain(db_session, is_public=True)
    _owner_b, _private_project, private_chain = await _seed_chain(db_session)
    await _add_hops(db_session, public_chain, [(0, "abstract_match", 1), (0, "no_match", 1)])
    await _add_hops(db_session, private_chain, [(0, "no_match", 50)])

    after = await bt_stats.load_coverage(db_session, public_only=True)

    assert after.chain_count - before.chain_count == 1
    assert after.resolved - before.resolved == 1
    assert after.attempted - before.attempted == 2

    scoped = await bt_stats.load_coverage(
        db_session, project_id=public_project.id, public_only=True
    )
    assert scoped.resolution_rate == pytest.approx(0.5)


# ── Endpoints ────────────────────────────────────────────────────────────────

async def test_chain_resolution_endpoint_returns_scoped_payload(db_session) -> None:
    owner, project, chain = await _seed_chain(db_session)
    await _add_hops(db_session, chain, [(0, "abstract_match", 1), (1, "no_match", 1)])

    out = await get_chain_resolution(chain.id, db_session, owner)

    assert out.chain_id == chain.id
    assert out.project_id == project.id
    assert out.resolved == 1
    assert out.unresolved == 1
    assert out.resolution_rate == pytest.approx(0.5)
    assert [d.depth for d in out.by_depth] == [1, 2]


async def test_public_endpoint_ignores_private_trees(db_session) -> None:
    """Measured across all public projects, so it compares before and after:
    a private tree must not change the published response at all."""
    before = await get_backtrace_resolution(db_session, project_id=None)

    _owner, _project, chain = await _seed_chain(db_session)
    await _add_hops(db_session, chain, [(0, "abstract_match", 3)])

    after = await get_backtrace_resolution(db_session, project_id=None)

    assert after == before


async def test_public_project_with_no_hops_reports_no_rate(db_session) -> None:
    """An unmeasured rate and a measured rate of zero are different claims."""
    _owner, project, _chain = await _seed_chain(db_session, is_public=True)

    out = await get_backtrace_resolution(db_session, project_id=project.id)

    assert out.chain_count == 0
    assert out.attempted == 0
    assert out.resolution_rate is None
