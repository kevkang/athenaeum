"""Bringing corpus nodes up to full-text evidence once a PDF has been parsed.

The second half of D-N15. An expansion places nodes with whatever text is in
hand — usually an abstract — and requests any open-access PDF in the
background. When ingestion of that PDF finishes, this runs for every node of
the paper, in every nucleus and project. It also serves the handoff's "supplying
the PDF triggers ingestion and evidence extraction for that node": an upload
reaches here by the same route.

Rules, each chosen so the upgrade can't quietly do the wrong thing:

- Rejected nodes are skipped, so nothing is spent on papers set aside.
- Nodes that already have full-text evidence are skipped: running twice is a
  no-op.
- A node with no statement gets one — but only if the archivist introduced
  it. A node a person added is theirs to describe.

Statements are no longer audited here, or anywhere in Nucleate (D-N22).
"""
from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain import nucleate as v
from athenaeum.nucleate.archivist import write_relevance
from athenaeum.provider.base import Provider
from athenaeum.storage.models import (
    CorpusNode,
    LineageEdge,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    Paper,
)
from athenaeum.synthesize.backtrace import search_full_text_for_claim

log = structlog.get_logger()

@dataclass
class UpgradeDeps:
    provider: Provider
    embed_model: str


async def _introducing_expansion(
    session: AsyncSession, review: NodeReview
) -> NucleusExpansion | None:
    """The expansion whose prompt brought this node into its nucleus, or None
    if a person added it."""
    return await session.scalar(
        select(NucleusExpansion)
        .join(LineageEdge, LineageEdge.prompt_id == NucleusExpansion.prompt_id)
        .where(LineageEdge.id == review.introduced_by_edge_id)
    )


async def upgrade_nodes_for_paper(
    session: AsyncSession,
    paper_id: uuid.UUID,
    deps_for: Callable[[Nucleus], Awaitable[UpgradeDeps]],
) -> Counter[str]:
    """Give every non-rejected node of `paper_id` full-text evidence, now that
    the paper has it. `deps_for` supplies model access per nucleus, since each
    runs on its own user's credentials. Returns what happened, by outcome."""
    outcomes: Counter[str] = Counter()
    paper = await session.get(Paper, paper_id)
    if paper is None or paper.status != "full_text":
        return outcomes

    rows = (await session.execute(
        select(NodeReview, Nucleus)
        .join(CorpusNode, CorpusNode.id == NodeReview.node_id)
        .join(Nucleus, Nucleus.id == NodeReview.nucleus_id)
        .where(CorpusNode.paper_id == paper_id)
    )).all()

    for review, nucleus in rows:
        if review.status == v.REJECTED:
            outcomes["skipped_rejected"] += 1
            continue
        existing = list(await session.scalars(
            select(NodeEvidence).where(NodeEvidence.review_id == review.id)
        ))
        if any(e.source == v.FULL_TEXT for e in existing):
            outcomes["already_full_text"] += 1
            continue

        deps = await deps_for(nucleus)
        # The paper may be linked to no project's library; its place in this
        # corpus is the authorization for reading it.
        passage = await search_full_text_for_claim(
            deps.provider, session, nucleus.project_id, paper.id, nucleus.question,
            deps.embed_model, link_to_project=False,
        )
        if passage is None or not passage.text_quote_selector:
            outcomes["no_full_text_evidence"] += 1
            continue

        next_ord = max((e.ord for e in existing), default=-1) + 1
        session.add(NodeEvidence(
            id=uuid.uuid4(), review_id=review.id, ord=next_ord, source=v.FULL_TEXT,
            verbatim_text=passage.verbatim_text, text_quote_selector=passage.text_quote_selector,
            passage_id=passage.id,
        ))
        await session.flush()
        outcomes["evidence_added"] += 1

        expansion = await _introducing_expansion(session, review)
        if expansion is None:
            # A person's node: the evidence is theirs to read, but no AI
            # statement is written for it; they chose it, not the archivist.
            await session.commit()
            continue

        if review.relevance_text is None:
            statement = await write_relevance(
                deps.provider, nucleus.question, paper, [(v.FULL_TEXT, passage.verbatim_text)]
            )
            if statement:
                review.relevance_text = statement
                review.relevance_provenance = v.AI_PROPOSED
                outcomes["statement_written"] += 1

        await session.commit()

    log.info("nucleate.fulltext_upgrade", paper_id=str(paper_id), **outcomes)
    return outcomes
