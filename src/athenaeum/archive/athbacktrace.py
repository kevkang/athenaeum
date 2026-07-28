"""Export/import a single backtrace tree as a portable `.athbacktrace` file —
a scoped-down sibling of the `.ath` project archive, meant to leave the
project entirely: shared with a colleague, archived independently, or
re-imported into a different project.

Unlike `.ath` import, which is identity-preserving (fails if the archive's
project UUID already exists, papers reconciled by `ON CONFLICT DO NOTHING`
at their literal exported UUID), this merges into an *already-open* project:
the chain and every hop get fresh ids on import (so importing the same tree
twice, or into two different projects, never collides), and target papers
resolve to whatever UUID they already have in the receiving DB — via
`find_or_create_target_paper`'s DOI-then-OpenAlex-then-ghost-create lookup,
the same helper live backtracing itself uses — rather than being reinserted
under their exporting-DB id.

Hops are walked and re-inserted in `hop_index` order, which by construction
(hop_index = parent.hop_index + 1) already guarantees every parent is
processed before its children — no separate topological sort needed, and a
single pass through the list is enough to build the old-id -> new-id map
incrementally.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.ingest.openalex import OpenAlexMeta
from athenaeum.ingest.pipeline import find_or_create_target_paper
from athenaeum.storage.models import BacktraceChain, BacktraceHop, Paper

ATHBACKTRACE_VERSION = 1


def _s(v: uuid.UUID | None) -> str | None:
    return str(v) if v is not None else None


async def build_athbacktrace(session: AsyncSession, chain_id: uuid.UUID) -> dict[str, Any]:
    chain = await session.get(BacktraceChain, chain_id)
    if chain is None:
        raise ValueError(f"Chain {chain_id} not found")

    origin_paper = await session.get(Paper, chain.origin_paper_id)
    hops = list(
        await session.scalars(
            select(BacktraceHop)
            .where(BacktraceHop.chain_id == chain_id)
            .order_by(BacktraceHop.hop_index)
        )
    )

    return {
        "athbacktrace_version": ATHBACKTRACE_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "origin_paper": {
            "doi": origin_paper.doi if origin_paper else None,
            "openalex_id": origin_paper.openalex_id if origin_paper else None,
            "title": origin_paper.title if origin_paper else None,
            "authors": origin_paper.authors_json if origin_paper else None,
            "venue": origin_paper.venue if origin_paper else None,
            "year": origin_paper.year if origin_paper else None,
        },
        "chain": {
            # origin_sentence_id is deliberately not exported — it points at
            # a PaperSentence row from a specific ingestion of the origin
            # paper, which has no guaranteed counterpart in the receiving
            # project even when the paper itself gets reused. origin_text
            # (the actual claim, verbatim) is what continuing the trace
            # actually needs, and that always survives.
            "origin_text": chain.origin_text,
        },
        "hops": [
            {
                "id": _s(h.id),
                "parent_hop_id": _s(h.parent_hop_id),
                "hop_index": h.hop_index,
                "to_openalex_id": h.to_openalex_id,
                "to_doi": h.to_doi,
                "to_title": h.to_title,
                "to_authors_json": h.to_authors_json,
                "to_year": h.to_year,
                "to_venue": h.to_venue,
                # A UUID, not whether a paper was ever linked — re-resolved
                # on import via find_or_create_target_paper rather than
                # reused literally, since it won't be valid in another DB.
                "to_paper_resolved": h.to_paper_id is not None,
                "quote": h.quote,
                "quote_source": h.quote_source,
                "relevance_note": h.relevance_note,
                "match_reasoning": h.match_reasoning,
                "model": h.model,
                "resolution_status": h.resolution_status,
                "user_decision": h.user_decision,
            }
            for h in hops
        ],
    }


def _meta_from_dict(d: dict[str, Any], *, title_key: str, doi_key: str, openalex_key: str,
                     authors_key: str, venue_key: str, year_key: str) -> OpenAlexMeta:
    return OpenAlexMeta(
        openalex_id=d.get(openalex_key),
        doi=d.get(doi_key),
        title=d.get(title_key) or "Untitled",
        authors=d.get(authors_key) or [],
        venue=d.get(venue_key),
        year=d.get(year_key),
        abstract=None,
        oa_pdf_url=None,
    )


async def import_athbacktrace(
    session: AsyncSession, data: dict[str, Any], project_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    origin_paper_data = data.get("origin_paper") or {}
    origin_meta = _meta_from_dict(
        origin_paper_data, title_key="title", doi_key="doi", openalex_key="openalex_id",
        authors_key="authors", venue_key="venue", year_key="year",
    )
    origin_paper = await find_or_create_target_paper(session, project_id, user_id, origin_meta)

    chain = BacktraceChain(
        id=uuid.uuid4(),
        project_id=project_id,
        created_by=user_id,
        origin_paper_id=origin_paper.id,
        origin_sentence_id=None,
        origin_text=data["chain"]["origin_text"],
    )
    session.add(chain)
    await session.flush()

    id_map: dict[str, uuid.UUID] = {}
    for h in data.get("hops", []):
        new_id = uuid.uuid4()
        id_map[h["id"]] = new_id

        to_paper_id = None
        if h.get("to_paper_resolved"):
            hop_meta = _meta_from_dict(
                {**h, "authors": [{"name": n} for n in (h.get("to_authors_json") or [])]},
                title_key="to_title", doi_key="to_doi", openalex_key="to_openalex_id",
                authors_key="authors", venue_key="to_venue", year_key="to_year",
            )
            target_paper = await find_or_create_target_paper(session, project_id, user_id, hop_meta)
            to_paper_id = target_paper.id

        parent_hop_id_str = h.get("parent_hop_id")
        session.add(BacktraceHop(
            id=new_id,
            chain_id=chain.id,
            parent_hop_id=id_map[parent_hop_id_str] if parent_hop_id_str else None,
            hop_index=h["hop_index"],
            to_openalex_id=h["to_openalex_id"],
            to_doi=h.get("to_doi"),
            to_title=h["to_title"],
            to_authors_json=h.get("to_authors_json"),
            to_year=h.get("to_year"),
            to_venue=h.get("to_venue"),
            to_paper_id=to_paper_id,
            quote=h.get("quote"),
            quote_source=h.get("quote_source"),
            relevance_note=h.get("relevance_note"),
            match_reasoning=h.get("match_reasoning"),
            model=h.get("model"),
            resolution_status=h.get("resolution_status", "pending"),
            user_decision=h.get("user_decision", "pending"),
        ))
        # Plain FK column, no ORM relationship() back to BacktraceHop —
        # nothing infers insert order otherwise, and a child hop later in
        # this same list needs its parent's row to already exist.
        await session.flush()

    await session.commit()
    return chain.id
