"""The one place a paper enters a nucleus.

Membership of a nucleus is defined by lineage edges, and every node in a
nucleus needs a review to hold the user's judgement of it (D-N11). Those two
facts have to move together, so nothing else writes either: the database
guarantees a review never exists without its introducing edge (a composite
foreign key), and this module guarantees an edge never arrives without a
review. tests/test_nucleate_schema.py checks that no other module constructs
a LineageEdge or NodeReview.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.nucleate import (
    FROM_NODE,
    FROM_NUCLEUS,
    FRONTIER,
    REJECTED,
    RetrievalSource,
)
from athenaeum.storage.models import CorpusNode, LineageEdge, NodeReview, Nucleus


class RejectedUnderThisNucleus(Exception):
    """The archivist tried to propose a paper the user already rejected under
    the same nucleus. The handoff forbids that outright; rejection is steering
    signal, and ignoring it would make rejecting pointless."""


@dataclass(frozen=True)
class Placement:
    node: CorpusNode
    edge: LineageEdge
    review: NodeReview
    # False when the node was already in this nucleus and this is simply
    # another path to it. Its review — the user's judgement — is left alone.
    new_to_nucleus: bool


async def place_node(
    session: AsyncSession,
    nucleus: Nucleus,
    paper_id: uuid.UUID,
    *,
    from_node_id: uuid.UUID | None = None,
    prompt_id: uuid.UUID | None = None,
    retrieval_source: RetrievalSource | None = None,
) -> Placement:
    """Put a paper into a nucleus, recording how it got there.

    - The corpus node is shared across nuclei: fetched if the project already
      has this paper, created otherwise.
    - A lineage edge is *always* recorded. A paper found again by another
      prompt, or from another node, is another path, and lineage is the record
      of how the corpus grew.
    - A review is created only the first time the node enters this nucleus,
      starting at frontier. An existing review is never touched: it is the
      user's judgement, and a second path to a paper is not a reason to undo it.

    `prompt_id` and `retrieval_source` come together (the archivist retrieved
    it) or not at all (a person added it — "Add to corpus" from Backtrace).
    Only the archivist is refused a paper rejected under this nucleus; a person
    re-adding it is making a deliberate choice.
    """
    if (prompt_id is None) != (retrieval_source is None):
        raise ValueError("prompt_id and retrieval_source are given together or not at all")

    node = await session.scalar(
        select(CorpusNode).where(
            CorpusNode.project_id == nucleus.project_id, CorpusNode.paper_id == paper_id
        )
    )
    if node is None:
        node = CorpusNode(id=uuid.uuid4(), project_id=nucleus.project_id, paper_id=paper_id)
        session.add(node)
        await session.flush()

    review = await session.scalar(
        select(NodeReview).where(
            NodeReview.nucleus_id == nucleus.id, NodeReview.node_id == node.id
        )
    )
    if review is not None and review.status == REJECTED and prompt_id is not None:
        raise RejectedUnderThisNucleus(
            f"paper {paper_id} was rejected under nucleus {nucleus.id}"
        )

    edge = LineageEdge(
        id=uuid.uuid4(),
        nucleus_id=nucleus.id,
        from_kind=FROM_NODE if from_node_id else FROM_NUCLEUS,
        from_node_id=from_node_id,
        to_node_id=node.id,
        prompt_id=prompt_id,
        retrieval_source=retrieval_source,
    )
    session.add(edge)
    await session.flush()

    if review is not None:
        return Placement(node=node, edge=edge, review=review, new_to_nucleus=False)

    review = NodeReview(
        id=uuid.uuid4(),
        nucleus_id=nucleus.id,
        node_id=node.id,
        introduced_by_edge_id=edge.id,
        status=FRONTIER,
    )
    session.add(review)
    await session.flush()
    return Placement(node=node, edge=edge, review=review, new_to_nucleus=True)


async def rejected_paper_ids(session: AsyncSession, nucleus_id: uuid.UUID) -> set[uuid.UUID]:
    """Papers rejected under this nucleus, for the ranking stage to drop
    before it spends anything on them. place_node refuses them too; this lets
    the pipeline avoid asking."""
    rows = await session.scalars(
        select(CorpusNode.paper_id)
        .join(NodeReview, NodeReview.node_id == CorpusNode.id)
        .where(NodeReview.nucleus_id == nucleus_id, NodeReview.status == REJECTED)
    )
    return set(rows)


async def member_node_ids(session: AsyncSession, nucleus_id: uuid.UUID) -> set[uuid.UUID]:
    """The nodes in a nucleus — by definition, those with a lineage edge in it."""
    rows = await session.scalars(
        select(LineageEdge.to_node_id).where(LineageEdge.nucleus_id == nucleus_id).distinct()
    )
    return set(rows)
