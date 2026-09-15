"""Resolution coverage for Backtrace citation trees: of the hops a user
actually chased, what fraction landed on a verbatim quote — broken out by
how far back from the origin claim they went.

Lives in domain/ rather than in either API module because two callers need
the *identical* definition (the per-tree endpoint behind the tree view's
depth rail, and the public cross-project aggregate), and a published
honesty statistic that quietly means two different things in two places is
worse than not publishing one at all. Same reasoning as domain/citations.py
being the single numbering pass for both the editor and the exporter.

Two things this number deliberately is not:

1. **The denominator is attempted hops, not available references.** A tree
   contains only the references a human chose to chase, so this measures
   "when we went looking, did we find it" — not "what share of this paper's
   bibliography is reachable." The candidate pool size that `/rank` saw is
   not persisted anywhere, so the second question is currently unanswerable;
   see ResolutionCoverage.attempted.
2. **Depth is where the decay shows up.** A tree with a healthy overall rate
   can still have tips that almost never resolve, because reachability falls
   off going backward in time (older work is less likely to have an open-
   access full text or a machine-readable abstract). The by-depth breakdown
   is the number that tells you whether calling a tip an origin is defensible;
   the global rate can hide it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.models import BacktraceDepthCoverageOut, BacktraceResolutionOut
from athenaeum.storage.models import BacktraceChain, BacktraceHop, Project

# A hop that found a verbatim quote for the claim, in the target's abstract
# or in its ingested full text. These are the only two successes.
RESOLVED_STATES = frozenset({"abstract_match", "full_text_match"})

# A hop that finished looking and came back empty. `awaiting_manual_import`
# counts as unresolved rather than in-flight on purpose: the automatic path
# has given up and handed the work back to the user, which may never happen.
# Counting it as "still going" would inflate the rate by hiding the failures
# in a bucket that never resolves.
UNRESOLVED_STATES = frozenset({"no_match", "awaiting_manual_import"})

# Still working. Excluded from both numerator and denominator, so a rate is
# never diluted by hops that simply haven't finished yet.
IN_FLIGHT_STATES = frozenset({"pending", "ingesting"})


@dataclass(frozen=True)
class DepthCoverage:
    """Counts for a single depth level of a tree (or across many trees)."""

    # 1-based, matching list_chains' existing `max_depth = max_index + 1`
    # convention: BacktraceHop.hop_index 0 means "one hop back from the
    # origin paper", which is depth 1 to a reader.
    depth: int
    resolved: int
    unresolved: int
    in_flight: int

    @property
    def attempted(self) -> int:
        return self.resolved + self.unresolved

    @property
    def resolution_rate(self) -> float | None:
        """None rather than 0.0 when nothing has finished — an unmeasured
        rate and a measured rate of zero are different claims, and the UI
        must be able to tell them apart."""
        return (self.resolved / self.attempted) if self.attempted else None


@dataclass(frozen=True)
class ResolutionCoverage:
    """Whole-scope totals plus the per-depth breakdown."""

    chain_count: int
    by_depth: tuple[DepthCoverage, ...]

    @property
    def resolved(self) -> int:
        return sum(d.resolved for d in self.by_depth)

    @property
    def unresolved(self) -> int:
        return sum(d.unresolved for d in self.by_depth)

    @property
    def in_flight(self) -> int:
        return sum(d.in_flight for d in self.by_depth)

    @property
    def attempted(self) -> int:
        return self.resolved + self.unresolved

    @property
    def resolution_rate(self) -> float | None:
        return (self.resolved / self.attempted) if self.attempted else None

    @property
    def deepest_depth(self) -> int:
        return max((d.depth for d in self.by_depth), default=0)

    @property
    def deepest(self) -> DepthCoverage | None:
        """The deepest level that has any finished hops — the tip of the
        tree, and the level whose rate decides whether the tree-as-path-to-
        origin framing holds up. Levels where everything is still in flight
        are skipped; they have no rate to report yet."""
        finished = [d for d in self.by_depth if d.attempted]
        return max(finished, key=lambda d: d.depth) if finished else None


def _scoped(
    stmt: Select,
    *,
    chain_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    public_only: bool,
) -> Select:
    """Apply scope filters to a select over BacktraceHop, joining up to
    BacktraceChain/Project only when a filter actually needs them (a
    single-chain lookup doesn't, and the join would be dead weight)."""
    if public_only or project_id is not None:
        stmt = stmt.join(BacktraceChain, BacktraceHop.chain_id == BacktraceChain.id)
    if public_only:
        stmt = stmt.join(Project, BacktraceChain.project_id == Project.id).where(
            Project.is_public.is_(True)
        )
    if project_id is not None:
        stmt = stmt.where(BacktraceChain.project_id == project_id)
    if chain_id is not None:
        stmt = stmt.where(BacktraceHop.chain_id == chain_id)
    return stmt


async def load_coverage(
    session: AsyncSession,
    *,
    chain_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    public_only: bool = False,
) -> ResolutionCoverage:
    """Count hops by (depth, resolution_status) in one grouped pass, then
    fold the statuses into resolved/unresolved/in-flight buckets in Python.

    Deliberately computed on read rather than cached on the chain row: the
    ten-odd call sites that set `resolution_status` would each have to
    remember to refresh a denormalized copy, and a stale honesty statistic
    is a worse failure than a slightly slower query. `backtrace_hops.chain_id`
    is an indexed foreign key and the grouping is flat (hop_index already
    *is* depth — see BacktraceHop.parent_hop_id), so this is one cheap
    aggregate, not a recursive walk.
    """
    rows = (
        await session.execute(
            _scoped(
                select(
                    BacktraceHop.hop_index,
                    BacktraceHop.resolution_status,
                    func.count(BacktraceHop.id),
                ).select_from(BacktraceHop),
                chain_id=chain_id,
                project_id=project_id,
                public_only=public_only,
            ).group_by(BacktraceHop.hop_index, BacktraceHop.resolution_status)
        )
    ).all()

    # depth -> [resolved, unresolved, in_flight]
    buckets: dict[int, list[int]] = {}
    for hop_index, status, count in rows:
        tally = buckets.setdefault(hop_index + 1, [0, 0, 0])
        if status in RESOLVED_STATES:
            tally[0] += count
        elif status in UNRESOLVED_STATES:
            tally[1] += count
        else:
            tally[2] += count

    by_depth = tuple(
        DepthCoverage(depth=depth, resolved=r, unresolved=u, in_flight=f)
        for depth, (r, u, f) in sorted(buckets.items())
    )

    chain_count = await session.scalar(
        _scoped(
            select(func.count(func.distinct(BacktraceHop.chain_id))).select_from(BacktraceHop),
            chain_id=chain_id,
            project_id=project_id,
            public_only=public_only,
        )
    )

    return ResolutionCoverage(chain_count=chain_count or 0, by_depth=by_depth)


def _depth_out(d: DepthCoverage) -> BacktraceDepthCoverageOut:
    return BacktraceDepthCoverageOut(
        depth=d.depth,
        resolved=d.resolved,
        unresolved=d.unresolved,
        in_flight=d.in_flight,
        attempted=d.attempted,
        resolution_rate=d.resolution_rate,
    )


def to_out(
    coverage: ResolutionCoverage,
    *,
    chain_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> BacktraceResolutionOut:
    """Serialize coverage identically for every caller — the per-tree route
    and the public aggregate differ only in scope, never in what the numbers
    mean."""
    deepest = coverage.deepest
    return BacktraceResolutionOut(
        chain_id=chain_id,
        project_id=project_id,
        chain_count=coverage.chain_count,
        resolved=coverage.resolved,
        unresolved=coverage.unresolved,
        in_flight=coverage.in_flight,
        attempted=coverage.attempted,
        resolution_rate=coverage.resolution_rate,
        deepest_depth=coverage.deepest_depth,
        deepest=_depth_out(deepest) if deepest is not None else None,
        by_depth=[_depth_out(d) for d in coverage.by_depth],
    )
