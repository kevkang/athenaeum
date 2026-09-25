"""Reconstruct a project from a .ath archive dict.

Every server generates its own ids for papers and for the sentences it splits
them into, so an archive's paper and sentence ids only mean something on the
server that exported it. Import therefore resolves each paper to this server's
own copy first (by DOI, then OpenAlex id) and points everything at that. Each
imported quote is then checked against this server's text for its paper (see
domain/source_check.py) and, when its words are found, re-pointed at this
server's sentences. Quotes whose paper has no text here are kept and marked
source_unavailable; the caller is told which papers to try fetching.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.source_check import (
    check_passage,
    load_paper_sentences,
    repoint_highlight,
)
from athenaeum.storage.models import (
    Annotation,
    ComposeDocument,
    Dataset,
    Evaluation,
    Event,
    Highlight,
    Paper,
    PaperSentence,
    Passage,
    Project,
    ProjectEventSeq,
    ProjectMember,
    ProjectPaper,
    Statement,
    StatementEdge,
    StatementEvidence,
    Synthesis,
    SynthesisEvidence,
)

ATH_VERSION = 1


@dataclass
class ImportResult:
    project_id: uuid.UUID
    # Papers this server has no text for (and hasn't already failed to
    # ingest). The caller should try to fetch them; their quotes are re-checked
    # when ingestion finishes.
    papers_to_fetch: list[uuid.UUID] = field(default_factory=list)
    # Plain-language notes about archive content that couldn't be imported.
    warnings: list[str] = field(default_factory=list)


def _u(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


async def _resolve_paper(session: AsyncSession, p: dict[str, Any]) -> uuid.UUID:
    """Return this server's id for an archived paper, creating the row only if
    no copy exists here. A copy is found by DOI, then OpenAlex id, then the
    archive's own id (which only matches when re-importing on the same
    server). A newly created paper is a ghost: whatever the archive says about
    its status, this server has none of its text until it is ingested here."""
    existing = None
    if p.get("doi"):
        existing = await session.scalar(select(Paper).where(Paper.doi == p["doi"]))
    if existing is None and p.get("openalex_id"):
        existing = await session.scalar(select(Paper).where(Paper.openalex_id == p["openalex_id"]))
    if existing is None:
        existing = await session.get(Paper, uuid.UUID(p["id"]))
    if existing is not None:
        return existing.id

    paper = Paper(
        id=uuid.UUID(p["id"]),
        doi=p.get("doi"),
        openalex_id=p.get("openalex_id"),
        title=p["title"],
        authors_json=p.get("authors_json") or [],
        venue=p.get("venue"),
        year=p.get("year"),
        abstract=p.get("abstract"),
        status="ghost",
    )
    session.add(paper)
    await session.flush()
    return paper.id


async def import_project(
    session: AsyncSession,
    data: dict[str, Any],
    owner_id: uuid.UUID,
) -> ImportResult:
    if data.get("ath_version") != ATH_VERSION:
        raise ValueError(f"Unsupported .ath version: {data.get('ath_version')!r}")

    proj = data["project"]
    project_id = uuid.UUID(proj["id"])
    result = ImportResult(project_id=project_id)

    # Fail fast if project UUID already occupied.
    existing = await session.get(Project, project_id)
    if existing is not None:
        raise ValueError(f"Project {project_id} already exists in this database")

    session.add(Project(
        id=project_id,
        title=proj["title"],
        description=proj.get("description"),
        license=proj.get("license", "CC-BY-4.0"),
        owner_id=owner_id,
    ))
    session.add(ProjectMember(project_id=project_id, user_id=owner_id, role="owner"))
    await session.flush()

    # Papers: archive id -> this server's id, used for every later reference.
    paper_ids: dict[uuid.UUID, uuid.UUID] = {}
    for p in data.get("papers", []):
        local_id = await _resolve_paper(session, p)
        paper_ids[uuid.UUID(p["id"])] = local_id
        await session.execute(
            pg_insert(ProjectPaper)
            .values(project_id=project_id, paper_id=local_id, added_by=owner_id)
            .on_conflict_do_nothing()
        )
    await session.flush()

    def local_paper(archive_id: str) -> uuid.UUID:
        pid = uuid.UUID(archive_id)
        return paper_ids.get(pid, pid)

    # Each paper's sentences are loaded at most once, however many quotes cite it.
    sentence_cache: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}

    async def sentences_for(paper_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
        if paper_id not in sentence_cache:
            sentence_cache[paper_id] = await load_paper_sentences(session, paper_id)
        return sentence_cache[paper_id]

    # Passages: check every quote against this server's copy of its paper.
    imported_passage_ids: set[uuid.UUID] = set()
    for pa in data.get("passages", []):
        passage = Passage(
            id=uuid.UUID(pa["id"]),
            paper_id=local_paper(pa["paper_id"]),
            sentence_ids=[uuid.UUID(s) for s in pa.get("sentence_ids", [])],
            verbatim_text=pa["verbatim_text"],
            verified=pa.get("verified", False),
            verified_at=_dt(pa.get("verified_at")),
            checker_model=pa.get("checker_model"),
        )
        check_passage(passage, await sentences_for(passage.paper_id))
        session.add(passage)
        imported_passage_ids.add(passage.id)

    # Flush before Syntheses: SynthesisEvidence (added below, inside that
    # loop) has a plain FK column to Passage with no ORM relationship()
    # linking the two — nothing tells SQLAlchemy's flush to order Passage
    # inserts first otherwise, and it doesn't reliably infer it on its own.
    await session.flush()

    # Syntheses
    for s in data.get("syntheses", []):
        session.add(Synthesis(
            id=uuid.UUID(s["id"]),
            project_id=project_id,
            claim_text=s["claim_text"],
            synthesis_text=s.get("synthesis_text"),
            query_expansion_json=s.get("query_expansion_json"),
            retrieval_corpus=s.get("retrieval_corpus", "project"),
            model=s.get("model", ""),
            checker_model=s.get("checker_model"),
            verification_rate=s.get("verification_rate"),
            relevance_rate=s.get("relevance_rate"),
            # These rates were measured on another server. Marking the row keeps
            # them out of this server's public aggregates.
            imported_at=datetime.now(UTC),
            status=s.get("status", "done"),
        ))
        for e in s.get("evidence", []):
            session.add(SynthesisEvidence(
                synthesis_id=uuid.UUID(s["id"]),
                passage_id=uuid.UUID(e["passage_id"]),
                role=e["role"],
                ord=e["ord"],
            ))

    # Flush before Statements: built_from_synthesis_id is the same
    # unrelated-FK situation as above, this time Statement -> Synthesis.
    await session.flush()

    # Statements (insert first, then edges that reference other statements).
    # "bricks"/"rests_on"/"builds_on" are accepted as a fallback for archives
    # exported before the bricks -> statements rename; "cemented"/"cemented_at"
    # similarly for archives exported before the cement -> lock rename.
    raw_statements = data.get("statements", data.get("bricks", []))
    for s in raw_statements:
        status = "locked" if s["status"] == "cemented" else s["status"]
        session.add(Statement(
            id=uuid.UUID(s["id"]),
            project_id=project_id,
            author_id=_u(s.get("author_id")),
            type=s["type"],
            status=status,
            assertion=s["assertion"],
            built_from_synthesis_id=_u(s.get("built_from_synthesis_id")),
            locked_at=_dt(s.get("locked_at", s.get("cemented_at"))),
        ))

    await session.flush()

    # Passages created below from sentence citations, keyed by their id so a
    # sentence cited by several statements becomes one passage.
    citation_passages: set[uuid.UUID] = set()

    async def resolve_passage_target(
        target_id: uuid.UUID, r: dict[str, Any], assertion: str
    ) -> uuid.UUID | None:
        """Where a "passage" citation should point on this server, or None if
        it can't be imported.

        A real passage from the archive, or one already on this server, keeps
        its id. Otherwise the citation pointed straight at a sentence on the
        exporting server. When the archive carries its words, they become a
        passage here, checked like any other quote; the id is derived from the
        project and sentence id so repeated citations share one passage. An
        archive from before quotes were exported carries no words, and unless
        this server happens to have that very sentence there is nothing left to
        point at, so the citation is skipped with a warning.
        """
        if target_id in imported_passage_ids:
            return target_id
        if await session.get(Passage, target_id) is not None:
            return target_id
        if r.get("quote") and r.get("paper_id"):
            passage_id = uuid.uuid5(project_id, f"sentence-citation:{target_id}")
            if passage_id not in citation_passages:
                passage = Passage(
                    id=passage_id,
                    paper_id=local_paper(r["paper_id"]),
                    sentence_ids=[target_id],
                    verbatim_text=r["quote"],
                )
                check_passage(passage, await sentences_for(passage.paper_id))
                session.add(passage)
                citation_passages.add(passage_id)
            return passage_id
        if await session.get(PaperSentence, target_id):
            return target_id
        result.warnings.append(
            f'A citation on "{assertion[:80]}" pointed at a sentence this archive gives no '
            "words for (it was exported before quotes were included), so it was not imported."
        )
        return None

    for s in raw_statements:
        statement_id = uuid.UUID(s["id"])
        seen: set[tuple[str, uuid.UUID]] = set()
        for r in s.get("evidence", s.get("rests_on", [])):
            target_kind = "statement" if r["target_kind"] == "brick" else r["target_kind"]
            target_id: uuid.UUID | None = uuid.UUID(r["target_id"])
            if target_kind == "paper":
                target_id = local_paper(r["target_id"])
            elif target_kind == "passage":
                target_id = await resolve_passage_target(
                    uuid.UUID(r["target_id"]), r, s["assertion"]
                )
            if target_id is None or (target_kind, target_id) in seen:
                continue
            seen.add((target_kind, target_id))
            session.add(StatementEvidence(
                statement_id=statement_id,
                target_kind=target_kind,
                target_id=target_id,
                role=r["role"],
            ))
        for e in s.get("edges", []):
            session.add(StatementEdge(
                from_statement_id=statement_id,
                to_statement_id=uuid.UUID(e["to_statement_id"]),
                relation=e["relation"],
            ))
        # Old-format builds_on has no equivalent typed relation — best-effort
        # map it to a neutral "relates_to" edge (the DAG-ordering concept
        # itself, `ord`, no longer exists in the new edge model).
        for bo in s.get("builds_on", []):
            session.add(StatementEdge(
                from_statement_id=statement_id,
                to_statement_id=uuid.UUID(bo["parent_brick_id"]),
                relation="relates_to",
            ))

    # Annotations
    for a in data.get("annotations", []):
        target_kind = "statement" if a["target_kind"] == "brick" else a["target_kind"]
        session.add(Annotation(
            id=uuid.UUID(a["id"]),
            project_id=project_id,
            author_id=uuid.UUID(a["author_id"]),
            target_kind=target_kind,
            target_id=uuid.UUID(a["target_id"]),
            body=a["body"],
        ))

    # Compose document — one per project, may be absent.
    compose_document = data.get("compose_document")
    if compose_document is not None:
        session.add(ComposeDocument(
            id=uuid.UUID(compose_document["id"]),
            project_id=project_id,
            content_json=compose_document["content_json"],
            version=compose_document.get("version", 1),
            updated_by=_u(compose_document.get("updated_by")),
        ))

    # Highlights — personal bookmarks, mirror Passage's shape. They store no
    # check outcome, but are re-pointed at this server's sentences when their
    # words are found, so the reader can place them.
    for h in data.get("highlights", []):
        highlight = Highlight(
            id=uuid.UUID(h["id"]),
            project_id=project_id,
            paper_id=local_paper(h["paper_id"]),
            sentence_ids=[uuid.UUID(s) for s in h.get("sentence_ids", [])],
            verbatim_text=h["verbatim_text"],
            author_id=_u(h.get("author_id")),
        )
        repoint_highlight(highlight, await sentences_for(highlight.paper_id))
        session.add(highlight)

    # Backward-compat: v1 archives exported questions as a separate list.
    # Import them as statements with type='question'.
    for q in data.get("questions", []):
        session.add(Statement(
            id=uuid.UUID(q["id"]),
            project_id=project_id,
            author_id=_u(q.get("author_id")),
            type="question",
            status=q.get("status", "open"),
            assertion=q["body"],
            built_from_synthesis_id=None,
        ))

    # Evaluations
    for e in data.get("evaluations", []):
        session.add(Evaluation(
            id=uuid.UUID(e["id"]),
            synthesis_id=uuid.UUID(e["synthesis_id"]),
            passage_id=uuid.UUID(e["passage_id"]),
            user_id=uuid.UUID(e["user_id"]),
            stance=e["stance"],
            body=e.get("body"),
        ))

    # Datasets
    for d in data.get("datasets", []):
        session.add(Dataset(
            id=uuid.UUID(d["id"]),
            project_id=project_id,
            kind=d["kind"],
            title=d["title"],
            storage_uri=d.get("storage_uri"),
        ))

    # Event log (preserves project_seq when present; otherwise assigns sequentially)
    raw_events = data.get("provenance", [])
    next_seq = 1
    for p in raw_events:
        seq = p.get("project_seq") or next_seq
        next_seq = max(next_seq, seq + 1)
        session.add(Event(
            id=uuid.UUID(p["id"]),
            project_id=project_id,
            project_seq=seq,
            actor_id=_u(p.get("actor_id")),
            kind=p["kind"],
            entity_kind=p.get("entity_kind") or "unknown",
            entity_id=_u(p.get("entity_id")),
            prev_json=p.get("prev_json"),
            payload_json=p["payload_json"],
        ))
    if raw_events:
        session.add(ProjectEventSeq(project_id=project_id, next_seq=next_seq))

    # Papers worth fetching: no text on this server, a DOI to look them up by,
    # and not already known to fail.
    for paper_id in dict.fromkeys(paper_ids.values()):
        paper = await session.get(Paper, paper_id)
        if (
            paper is not None
            and paper.doi
            and paper.status != "failed"
            and not await sentences_for(paper_id)
        ):
            result.papers_to_fetch.append(paper_id)

    await session.commit()
    return result
