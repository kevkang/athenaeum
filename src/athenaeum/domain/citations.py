"""Shared citation numbering for Compose documents.

A Compose document's prose links `Statement`s via the `statementLink` mark,
and each `Statement` can carry several `StatementEvidence` rows. This module
is the one place that turns those rows into the two-level numbering a reader
sees — a reference number per distinct cited source (paper/dataset/
statement), and a decimal sub-id per distinct piece of evidence within that
reference (`1.1`, `1.2`, `2.1`, ...) — so the live Compose editor's hover UI
and the `.athmd` exporter can never compute two different numberings for the
same document. Numbers are derived fresh from `StatementEvidence` on every
call; nothing here is persisted.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.storage.models import (
    Dataset,
    Paper,
    PaperSentence,
    Passage,
    Statement,
    StatementEvidence,
)

ReferenceKind = Literal["paper", "dataset", "statement"]


def iter_statement_ids_in_order(content_json: dict[str, Any]) -> list[uuid.UUID]:
    """Walk a ProseMirror doc top-to-bottom, collecting distinct
    `statementLink` `statementId`s in first-occurrence order. Marks live on
    individual text nodes, so a single logical span can be split across
    several adjacent nodes (e.g. partly bold) — this only needs the set of
    ids and their first appearance, not span boundaries, so that's fine."""
    seen: set[str] = set()
    ordered: list[uuid.UUID] = []

    def _walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for mark in node.get("marks") or []:
            if mark.get("type") == "statementLink":
                sid = (mark.get("attrs") or {}).get("statementId")
                if sid and sid not in seen:
                    seen.add(sid)
                    ordered.append(uuid.UUID(sid))
        for child in node.get("content") or []:
            _walk(child)

    _walk(content_json)
    return ordered


@dataclass(frozen=True)
class ReferenceKey:
    """The identity 'what is being cited' groups on. Paper- and
    passage-kind evidence collapse onto the same key when they resolve to
    the same paper_id — citing a paper generally and citing a specific
    passage from it are the same reference, just different evidence
    granularity."""
    kind: ReferenceKind
    id: uuid.UUID


@dataclass
class EvidenceEntry:
    sub_id: str  # e.g. "1.2"
    target_kind: str
    target_id: uuid.UUID
    quote: str | None  # None for a general/paper-level citation with no specific passage
    role: str


@dataclass
class ReferenceEntry:
    number: int
    key: ReferenceKey
    paper: Paper | None = None
    dataset: Dataset | None = None
    statement: Statement | None = None
    evidence: list[EvidenceEntry] = field(default_factory=list)


@dataclass
class CitationNumbering:
    references: list[ReferenceEntry]
    # statement_id -> the sub-ids that statement's own evidence resolves to,
    # in a deterministic (not necessarily insertion) order. Empty for a
    # statement with no evidence yet.
    statement_markers: dict[uuid.UUID, list[str]]


async def resolve_evidence_reference(
    session: AsyncSession, target_kind: str, target_id: uuid.UUID
) -> tuple[ReferenceKey, str | None]:
    """Resolve a single StatementEvidence target to a stable reference
    identity plus its verbatim quote, if it has one. A single-row
    convenience wrapper around the bulk resolution `build_reference_numbering`
    uses internally — prefer that for numbering more than one row, since this
    issues its own query per call."""
    keys, quotes = await _resolve_references_bulk(session, [(target_kind, target_id)])
    key = keys[(target_kind, target_id)]
    return key, quotes[(target_kind, target_id)]


async def _resolve_references_bulk(
    session: AsyncSession, targets: list[tuple[str, uuid.UUID]]
) -> tuple[dict[tuple[str, uuid.UUID], ReferenceKey], dict[tuple[str, uuid.UUID], str | None]]:
    """Resolve many (target_kind, target_id) pairs at once, in a handful of
    batched queries rather than one per row."""
    keys: dict[tuple[str, uuid.UUID], ReferenceKey] = {}
    quotes: dict[tuple[str, uuid.UUID], str | None] = {}

    unknown_kinds = {kind for kind, _ in targets} - {"paper", "passage", "dataset", "statement"}
    if unknown_kinds:
        raise ValueError(f"Unknown target_kind(s): {unknown_kinds}")

    for kind, ref_kind in (("paper", "paper"), ("dataset", "dataset"), ("statement", "statement")):
        for _kind, tid in targets:
            if _kind == kind:
                keys[(kind, tid)] = ReferenceKey(ref_kind, tid)  # type: ignore[arg-type]
                quotes[(kind, tid)] = None

    passage_target_ids = list({tid for kind, tid in targets if kind == "passage"})
    if passage_target_ids:
        passages = list(
            await session.scalars(select(Passage).where(Passage.id.in_(passage_target_ids)))
        )
        passages_by_id = {p.id: p for p in passages}
        for tid in passage_target_ids:
            p = passages_by_id.get(tid)
            if p is not None:
                keys[("passage", tid)] = ReferenceKey("paper", p.paper_id)
                quotes[("passage", tid)] = p.verbatim_text

        # Evidence dragged straight from the reader pane points at a
        # PaperSentence, not a synthesis-grade Passage — same fallback as
        # api/passages.py::get_passage.
        remaining = [tid for tid in passage_target_ids if tid not in passages_by_id]
        if remaining:
            sentences = list(
                await session.scalars(select(PaperSentence).where(PaperSentence.id.in_(remaining)))
            )
            sentences_by_id = {s.id: s for s in sentences}
            for tid in remaining:
                s = sentences_by_id.get(tid)
                if s is None:
                    raise ValueError(
                        f"Evidence target {tid} is neither a Passage nor a PaperSentence"
                    )
                keys[("passage", tid)] = ReferenceKey("paper", s.paper_id)
                quotes[("passage", tid)] = s.text

    return keys, quotes


async def build_reference_numbering(
    session: AsyncSession, statement_ids_in_order: list[uuid.UUID]
) -> CitationNumbering:
    """The single shared numbering pass: bulk-fetches every StatementEvidence
    row for the given statements, resolves each to a reference identity, and
    assigns reference numbers and evidence sub-ids by first document-order
    appearance."""
    if not statement_ids_in_order:
        return CitationNumbering(references=[], statement_markers={})

    evidence_rows = list(
        await session.scalars(
            select(StatementEvidence).where(
                StatementEvidence.statement_id.in_(statement_ids_in_order)
            )
        )
    )
    # StatementEvidence carries no `ord` column, so there's no persisted
    # order for evidence attached to the same statement — sort by target
    # identity for a deterministic (not arbitrary-dict-order) tiebreak that
    # produces the same numbering across repeated calls.
    evidence_rows.sort(key=lambda e: (str(e.statement_id), e.target_kind, str(e.target_id)))

    targets = [(e.target_kind, e.target_id) for e in evidence_rows]
    keys, quotes = await _resolve_references_bulk(session, targets)

    evidence_by_statement: dict[uuid.UUID, list[StatementEvidence]] = {}
    for e in evidence_rows:
        evidence_by_statement.setdefault(e.statement_id, []).append(e)

    reference_numbers: dict[ReferenceKey, int] = {}
    reference_order: list[ReferenceKey] = []
    for sid in statement_ids_in_order:
        for e in evidence_by_statement.get(sid, []):
            key = keys[(e.target_kind, e.target_id)]
            if key not in reference_numbers:
                reference_numbers[key] = len(reference_order) + 1
                reference_order.append(key)

    sub_id_by_target: dict[tuple[str, uuid.UUID], str] = {}
    evidence_by_reference: dict[ReferenceKey, list[EvidenceEntry]] = {
        key: [] for key in reference_order
    }
    for sid in statement_ids_in_order:
        for e in evidence_by_statement.get(sid, []):
            target = (e.target_kind, e.target_id)
            if target not in sub_id_by_target:
                key = keys[target]
                sub_id = f"{reference_numbers[key]}.{len(evidence_by_reference[key]) + 1}"
                sub_id_by_target[target] = sub_id
                evidence_by_reference[key].append(
                    EvidenceEntry(
                        sub_id=sub_id, target_kind=e.target_kind, target_id=e.target_id,
                        quote=quotes[target], role=e.role,
                    )
                )

    statement_markers: dict[uuid.UUID, list[str]] = {}
    for sid in statement_ids_in_order:
        markers: list[str] = []
        for e in evidence_by_statement.get(sid, []):
            sub_id = sub_id_by_target[(e.target_kind, e.target_id)]
            if sub_id not in markers:
                markers.append(sub_id)
        statement_markers[sid] = markers

    paper_ids = [k.id for k in reference_order if k.kind == "paper"]
    dataset_ids = [k.id for k in reference_order if k.kind == "dataset"]
    statement_ref_ids = [k.id for k in reference_order if k.kind == "statement"]

    papers_by_id = {
        p.id: p for p in (
            list(await session.scalars(select(Paper).where(Paper.id.in_(paper_ids))))
            if paper_ids else []
        )
    }
    datasets_by_id = {
        d.id: d for d in (
            list(await session.scalars(select(Dataset).where(Dataset.id.in_(dataset_ids))))
            if dataset_ids else []
        )
    }
    statement_stmt = select(Statement).where(Statement.id.in_(statement_ref_ids))
    statements_by_id = {
        s.id: s for s in (
            list(await session.scalars(statement_stmt)) if statement_ref_ids else []
        )
    }

    references = [
        ReferenceEntry(
            number=reference_numbers[key],
            key=key,
            paper=papers_by_id.get(key.id) if key.kind == "paper" else None,
            dataset=datasets_by_id.get(key.id) if key.kind == "dataset" else None,
            statement=statements_by_id.get(key.id) if key.kind == "statement" else None,
            evidence=evidence_by_reference[key],
        )
        for key in reference_order
    ]

    return CitationNumbering(references=references, statement_markers=statement_markers)
