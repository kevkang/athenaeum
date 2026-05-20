"""Hybrid retrieval: pgvector cosine similarity + Postgres FTS, fused with RRF."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.models import PaperSnippet, SentenceHit
from athenaeum.storage.models import Paper, PaperSection, PaperSentence, ProjectPaper

_RRF_K = 60  # standard constant; lower values weight high-rank hits more


def _vec_str(embedding: list[float]) -> str:
    """Format a Python list as a Postgres vector literal."""
    return "[" + ",".join(str(v) for v in embedding) + "]"


def _rrf_merge(
    *ranked_lists: list[uuid.UUID],
) -> list[tuple[uuid.UUID, float]]:
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        for rank, hit_id in enumerate(ranked, start=1):
            scores[hit_id] = scores.get(hit_id, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


async def _vector_search(
    session: AsyncSession,
    project_id: uuid.UUID,
    embedding: list[float],
    k: int,
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text("""
            SELECT
                ps.id,
                ps.paper_id,
                ps.section_id,
                ps.text,
                ps.char_start,
                ps.char_end,
                (1 - (ps.embedding <=> :vec::vector))::float AS score
            FROM paper_sentences ps
            JOIN project_papers pp ON pp.paper_id = ps.paper_id
            WHERE pp.project_id = :pid
              AND ps.embedding IS NOT NULL
            ORDER BY ps.embedding <=> :vec::vector
            LIMIT :k
        """),
        {"vec": _vec_str(embedding), "pid": str(project_id), "k": k},
    )
    return [dict(r._mapping) for r in rows]


async def _text_search(
    session: AsyncSession,
    project_id: uuid.UUID,
    query_text: str,
    k: int,
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text("""
            SELECT
                ps.id,
                ps.paper_id,
                ps.section_id,
                ps.text,
                ps.char_start,
                ps.char_end,
                ts_rank_cd(ps.text_search, plainto_tsquery('english', :q))::float AS score
            FROM paper_sentences ps
            JOIN project_papers pp ON pp.paper_id = ps.paper_id
            WHERE pp.project_id = :pid
              AND ps.text_search @@ plainto_tsquery('english', :q)
            ORDER BY score DESC
            LIMIT :k
        """),
        {"q": query_text, "pid": str(project_id), "k": k},
    )
    return [dict(r._mapping) for r in rows]


async def _enrich(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    scores: dict[uuid.UUID, float],
) -> list[SentenceHit]:
    if not rows:
        return []

    ids = [r["id"] for r in rows]

    enriched_rows = await session.execute(
        select(
            PaperSentence.id,
            PaperSentence.text,
            PaperSentence.char_start,
            PaperSentence.char_end,
            PaperSection.label.label("section_label"),
            Paper.id.label("paper_id"),
            Paper.title.label("paper_title"),
            Paper.doi,
            Paper.year,
            Paper.venue,
        )
        .join(PaperSection, PaperSection.id == PaperSentence.section_id)
        .join(Paper, Paper.id == PaperSentence.paper_id)
        .where(PaperSentence.id.in_(ids))
    )

    by_id = {r.id: r for r in enriched_rows}

    hits: list[SentenceHit] = []
    for raw in rows:
        row = by_id.get(raw["id"])
        if row is None:
            continue
        hits.append(
            SentenceHit(
                sentence_id=row.id,
                text=row.text,
                char_start=row.char_start,
                char_end=row.char_end,
                section_label=row.section_label,
                score=scores.get(row.id, 0.0),
                paper=PaperSnippet(
                    id=row.paper_id,
                    title=row.paper_title,
                    doi=row.doi,
                    year=row.year,
                    venue=row.venue,
                ),
            )
        )
    return hits


async def search(
    session: AsyncSession,
    project_id: uuid.UUID,
    query_text: str,
    query_embedding: list[float] | None,
    top_k: int = 20,
    mode: str = "hybrid",
) -> list[SentenceHit]:
    inner_k = min(top_k * 4, 200)

    vec_hits: list[dict[str, Any]] = []
    txt_hits: list[dict[str, Any]] = []

    if mode in ("vector", "hybrid") and query_embedding is not None:
        vec_hits = await _vector_search(session, project_id, query_embedding, inner_k)

    if mode in ("text", "hybrid"):
        txt_hits = await _text_search(session, project_id, query_text, inner_k)

    if mode == "hybrid":
        merged = _rrf_merge(
            [r["id"] for r in vec_hits],
            [r["id"] for r in txt_hits],
        )
        all_raw = {r["id"]: r for r in vec_hits}
        all_raw.update({r["id"]: r for r in txt_hits})
        top = merged[:top_k]
        ordered_rows = [all_raw[id_] for id_, _ in top if id_ in all_raw]
        scores = {id_: s for id_, s in top}
    elif mode == "vector":
        ordered_rows = vec_hits[:top_k]
        scores = {r["id"]: r["score"] for r in ordered_rows}
    else:
        ordered_rows = txt_hits[:top_k]
        scores = {r["id"]: r["score"] for r in ordered_rows}

    return await _enrich(session, ordered_rows, scores)
