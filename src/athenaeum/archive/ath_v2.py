"""`.ath` format version 2 (D-N3): the project archive as JSON-LD.

Version 2 is version 1's archive, moved from YAML to JSON, plus the Nucleate
corpus (handoff Part VI): nuclei with their prompts and runs, nodes with their
per-nucleus reviews and evidence, and lineage and semantic edges. One format
for the whole project, so the corpus and the rest of the archive can't drift
into two serializers.

Where it departs from the handoff's skeleton, and why:

- **Review state is per nucleus.** The skeleton puts status and relevance on
  the node; D-N11 made them per (paper, nucleus), so each node carries a list
  of `reviews`, one per nucleus it's in.
- **The @context is inline**, not a URL. The handoff's URL is a placeholder
  that resolves nowhere, and a context that can't be fetched makes a file
  that isn't valid JSON-LD. Inline, every file stands alone; the same context
  is also served at /formats/ath/2.0/context.jsonld. Tools that ignore linked
  data read plain JSON either way.
- **Relations are Athenaeum's terms** ("uses_method_of"), which the context
  maps to their CiTO IRIs, rather than compact IRIs in the data.

Nothing here carries a paper's full text: quotes, selectors, abstracts and
identifiers only.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain import nucleate as v
from athenaeum.domain.relations import jsonld_context
from athenaeum.nucleate.audits import latest_audits
from athenaeum.nucleate.corpus import place_node
from athenaeum.nucleate.retrieval import author_names
from athenaeum.storage.models import (
    CorpusNode,
    LineageEdge,
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusPrompt,
    Paper,
    SemanticEdge,
    SemanticEdgeEvidence,
    User,
)

FORMAT = "ath"
VERSION = "2.0"
MEDIA_TYPE = "application/ld+json"

# The W3C Web Annotation vocabulary, where TextQuoteSelector comes from.
OA_NS = "http://www.w3.org/ns/oa#"

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema" / "ath-v2.schema.json"


def context() -> dict[str, Any]:
    """The file's single @context. Namespaces, the relation terms, and the
    selector's words; nothing further into RDF than that (handoff Part VI)."""
    return {
        **jsonld_context(),
        "oa": OA_NS,
        # A relation's value is a term above, so it expands to its IRI.
        "relation": {"@id": "ath:relation", "@type": "@vocab"},
        "proposed_relation": {"@id": "ath:proposedRelation", "@type": "@vocab"},
        "selector": "oa:hasSelector",
        "TextQuoteSelector": "oa:TextQuoteSelector",
        "exact": "oa:exact",
        "prefix": "oa:prefix",
        "suffix": "oa:suffix",
    }


def schema() -> dict[str, Any]:
    """The published JSON Schema for this version."""
    loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    return loaded


def node_id(paper: Paper) -> str:
    """A node's identifier in the file: its DOI where it has one — the
    export identifier the handoff asks for — and its paper's UUID otherwise,
    since uploaded PDFs and some preprints have none."""
    return f"doi:{paper.doi}" if paper.doi else f"urn:uuid:{paper.id}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _s(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _evidence(source: str, selector: dict[str, str]) -> dict[str, Any]:
    return {"source": source, "selector": selector}


async def corpus_papers(session: AsyncSession, project_id: uuid.UUID) -> list[Paper]:
    """Papers in the project's corpus: every paper with a node, whether or not
    it is in the library (D-N18)."""
    return list(await session.scalars(
        select(Paper).join(CorpusNode, CorpusNode.paper_id == Paper.id)
        .where(CorpusNode.project_id == project_id)
    ))


async def build_corpus(session: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    """The Nucleate sections of a v2 archive: nuclei, nodes, edges."""
    nodes = list(await session.scalars(
        select(CorpusNode).where(CorpusNode.project_id == project_id)
        .order_by(CorpusNode.created_at)
    ))
    papers = {p.id: p for p in await corpus_papers(session, project_id)}
    ref = {n.id: node_id(papers[n.paper_id]) for n in nodes}

    nuclei = list(await session.scalars(
        select(Nucleus).where(Nucleus.project_id == project_id).order_by(Nucleus.created_at)
    ))
    nucleus_ids = [n.id for n in nuclei]
    prompts = list(await session.scalars(
        select(NucleusPrompt).where(NucleusPrompt.nucleus_id.in_(nucleus_ids))
        .order_by(NucleusPrompt.created_at)
    )) if nucleus_ids else []
    runs = {e.prompt_id: e for e in await session.scalars(
        select(NucleusExpansion).where(NucleusExpansion.nucleus_id.in_(nucleus_ids))
    )} if nucleus_ids else {}
    reviews = list(await session.scalars(
        select(NodeReview).where(NodeReview.nucleus_id.in_(nucleus_ids))
        .order_by(NodeReview.created_at)
    )) if nucleus_ids else []
    review_ids = [r.id for r in reviews]
    evidence: dict[uuid.UUID, list[NodeEvidence]] = {}
    for e in (await session.scalars(
        select(NodeEvidence).where(NodeEvidence.review_id.in_(review_ids))
        .order_by(NodeEvidence.ord)
    )) if review_ids else []:
        evidence.setdefault(e.review_id, []).append(e)
    audits = await latest_audits(session, review_ids)
    lineage = list(await session.scalars(
        select(LineageEdge).where(LineageEdge.nucleus_id.in_(nucleus_ids))
        .order_by(LineageEdge.created_at)
    )) if nucleus_ids else []
    semantic = list(await session.scalars(
        select(SemanticEdge).where(SemanticEdge.project_id == project_id)
        .order_by(SemanticEdge.created_at)
    ))
    semantic_evidence: dict[uuid.UUID, list[SemanticEdgeEvidence]] = {}
    for se in (await session.scalars(
        select(SemanticEdgeEvidence)
        .where(SemanticEdgeEvidence.edge_id.in_([e.id for e in semantic]))
        .order_by(SemanticEdgeEvidence.ord)
    )) if semantic else []:
        semantic_evidence.setdefault(se.edge_id, []).append(se)

    prompts_of: dict[uuid.UUID, list[NucleusPrompt]] = {}
    for p in prompts:
        prompts_of.setdefault(p.nucleus_id, []).append(p)
    reviews_of: dict[uuid.UUID, list[NodeReview]] = {}
    for r in reviews:
        reviews_of.setdefault(r.node_id, []).append(r)

    def run(e: NucleusExpansion | None) -> dict[str, Any] | None:
        if e is None:
            return None
        return {
            "status": e.status, "budget": e.budget, "model": e.model,
            "input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
            "reply_text": e.reply_text,
            "started": _iso(e.started_at), "finished": _iso(e.finished_at),
        }

    def review(r: NodeReview) -> dict[str, Any]:
        audit = audits.get(r.id)
        return {
            "nucleus": str(r.nucleus_id),
            "status": r.status,
            "rejection_reason": r.rejection_reason,
            "relevance": {
                "text": r.relevance_text,
                "provenance": r.relevance_provenance,
                "audit": {"supported": audit.supported, "rationale": audit.rationale,
                          "checked": _iso(audit.checked_at)} if audit else None,
            } if r.relevance_text is not None else None,
            "selection_reasoning": r.selection_reasoning,
            "evidence": [_evidence(e.source, e.text_quote_selector)
                         for e in evidence.get(r.id, [])],
        }

    def lineage_edge(e: LineageEdge) -> dict[str, Any]:
        return {
            "kind": "lineage",
            "nucleus": str(e.nucleus_id),
            "from_kind": e.from_kind,
            "from": str(e.nucleus_id) if e.from_kind == v.FROM_NUCLEUS
            else ref[e.from_node_id] if e.from_node_id else None,
            "to": ref[e.to_node_id],
            "prompt": _s(e.prompt_id),
            "retrieval_source": e.retrieval_source,
            "created": _iso(e.created_at),
        }

    def semantic_edge(e: SemanticEdge) -> dict[str, Any]:
        return {
            "kind": "semantic",
            "from": ref[e.from_node_id],
            "to": ref[e.to_node_id],
            "relation": e.relation,
            "proposed_relation": e.proposed_relation,
            "provenance": e.provenance,
            "rationale": e.rationale,
            "rejected": e.rejected_at is not None,
            "rejection_reason": e.rejection_reason,
            "evidence": [_evidence(x.source, x.text_quote_selector)
                         for x in semantic_evidence.get(e.id, [])],
            "created_by": _s(e.created_by),
            "created": _iso(e.created_at),
        }

    return {
        "nuclei": [
            {
                "id": str(n.id),
                "question": n.question,
                "created_by": _s(n.created_by),
                "created": _iso(n.created_at),
                "prompts": [
                    {
                        "id": str(p.id),
                        "text": p.text,
                        "anchor_nodes": [ref[a] for a in (p.anchor_node_ids or []) if a in ref],
                        "created_by": _s(p.created_by),
                        "created": _iso(p.created_at),
                        "run": run(runs.get(p.id)),
                    }
                    for p in prompts_of.get(n.id, [])
                ],
            }
            for n in nuclei
        ],
        "nodes": [
            {
                "id": ref[n.id],
                "type": "paper",
                "paper": str(n.paper_id),
                "metadata": {
                    "title": papers[n.paper_id].title,
                    "authors": author_names(papers[n.paper_id].authors_json),
                    "year": papers[n.paper_id].year,
                    "venue": papers[n.paper_id].venue,
                    "doi": papers[n.paper_id].doi,
                },
                "fulltext": v.fulltext_state(papers[n.paper_id].status,
                                             bool(papers[n.paper_id].abstract)),
                "reviews": [review(r) for r in reviews_of.get(n.id, [])],
            }
            for n in nodes
        ],
        "edges": [lineage_edge(e) for e in lineage] + [semantic_edge(e) for e in semantic],
    }



# ── Import ───────────────────────────────────────────────────────────────────


class InvalidArchive(ValueError):
    """The file isn't a valid version 2 archive."""


def validate(data: dict[str, Any]) -> None:
    """Check a version 2 archive against the published schema, before anything
    is written. The first problem is reported with where it is."""
    import jsonschema  # type: ignore[import-untyped]

    validator = jsonschema.Draft202012Validator(schema())
    error = next(iter(sorted(validator.iter_errors(data), key=lambda e: list(e.path))), None)
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "(top level)"
        raise InvalidArchive(f"Not a valid .ath 2.0 archive at {where}: {error.message}")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


async def import_corpus(
    session: AsyncSession,
    data: dict[str, Any],
    project_id: uuid.UUID,
    local_paper: Callable[[str], uuid.UUID],
) -> list[str]:
    """Recreate the Nucleate sections in a newly imported project. Returns
    plain-language warnings about anything not carried over.

    Lineage is replayed through place_node in the order it happened, so the
    rule that a review exists only with lineage holds on this server too, and
    each review is then set to what the file records. Original timestamps are
    kept: the canvas and the method record read in the order things happened.

    Evidence keeps the file's selectors. They anchor quotes by their words,
    which hold wherever the same text does, even before this server has a copy
    of the paper.
    """
    warnings: list[str] = []
    known_users = set(await session.scalars(select(User.id)))

    def user(value: str | None) -> uuid.UUID | None:
        """Keep an author only if they exist here; ids from another server
        otherwise point at no one."""
        uid = uuid.UUID(value) if value else None
        return uid if uid in known_users else None

    # Nodes: one per paper, created up front so nodes that are in no nucleus
    # (reached only by a relation) exist too.
    paper_of = {n["id"]: local_paper(n["paper"]) for n in data.get("nodes", [])}
    local_node: dict[str, uuid.UUID] = {}
    for ref, paper_id in paper_of.items():
        node = await session.scalar(select(CorpusNode).where(
            CorpusNode.project_id == project_id, CorpusNode.paper_id == paper_id))
        if node is None:
            node = CorpusNode(id=uuid.uuid4(), project_id=project_id, paper_id=paper_id)
            session.add(node)
        local_node[ref] = node.id
    await session.flush()

    # Nuclei, then prompts, then runs, each flushed before the next: these
    # tables share no ORM relationship, so nothing else tells the flush that
    # a prompt's row must exist before its run's.
    nuclei: dict[str, Nucleus] = {}
    prompts: dict[str, tuple[NucleusPrompt, list[str]]] = {}
    runs: list[NucleusExpansion] = []
    unfinished = 0
    for n in data.get("nuclei", []):
        nucleus = Nucleus(id=uuid.UUID(n["id"]), project_id=project_id, question=n["question"],
                          created_by=user(n.get("created_by")))
        if n.get("created"):
            nucleus.created_at = _dt(n["created"])  # type: ignore[assignment]
        session.add(nucleus)
        nuclei[n["id"]] = nucleus
    await session.flush()
    for n in data.get("nuclei", []):
        nucleus = nuclei[n["id"]]
        for p in n.get("prompts", []):
            prompt = NucleusPrompt(id=uuid.UUID(p["id"]), nucleus_id=nucleus.id, text=p["text"],
                                   anchor_node_ids=[], created_by=user(p.get("created_by")))
            if p.get("created"):
                prompt.created_at = _dt(p["created"])  # type: ignore[assignment]
            session.add(prompt)
            prompts[p["id"]] = (prompt, p.get("anchor_nodes", []))
            r = p.get("run")
            if r is not None:
                status = r["status"]
                if status == v.RUNNING:
                    # Nothing will ever finish it here.
                    status, unfinished = v.CANCELLED, unfinished + 1
                started = _dt(r.get("started")) or datetime.now(UTC)
                runs.append(NucleusExpansion(
                    id=uuid.uuid4(), nucleus_id=nucleus.id, prompt_id=prompt.id, status=status,
                    budget=r["budget"], model=r.get("model"), input_tokens=r["input_tokens"],
                    output_tokens=r["output_tokens"], reply_text=r.get("reply_text"),
                    started_at=started, finished_at=_dt(r.get("finished")) or started,
                ))
    await session.flush()
    session.add_all(runs)
    await session.flush()
    if unfinished:
        warnings.append(f"{unfinished} archivist step(s) were still running when exported; "
                        "they are imported as cancelled.")

    for prompt, anchors in prompts.values():
        prompt.anchor_node_ids = [local_node[a] for a in anchors if a in local_node]

    # Lineage, in the order it happened.
    for e in data.get("edges", []):
        if e["kind"] != "lineage":
            continue
        in_nucleus = nuclei.get(e["nucleus"])
        if in_nucleus is None or e["to"] not in paper_of:
            warnings.append("A lineage edge named a nucleus or paper the file doesn't contain; "
                            "it was skipped.")
            continue
        from_node = local_node.get(e["from"]) if e["from_kind"] == v.FROM_NODE else None
        prompt_id = uuid.UUID(e["prompt"]) if e.get("prompt") else None
        placed = await place_node(
            session, in_nucleus, paper_of[e["to"]], from_node_id=from_node,
            prompt_id=prompt_id if e.get("retrieval_source") else None,
            retrieval_source=e.get("retrieval_source") if prompt_id else None,
        )
        if e.get("created"):
            placed.edge.created_at = _dt(e["created"])  # type: ignore[assignment]
            if placed.new_to_nucleus:
                placed.review.created_at = placed.edge.created_at

    # Each review, as the file records it.
    audited = 0
    for n in data.get("nodes", []):
        for r in n.get("reviews", []):
            review = await session.scalar(select(NodeReview).where(
                NodeReview.nucleus_id == uuid.UUID(r["nucleus"]),
                NodeReview.node_id == local_node[n["id"]]))
            if review is None:
                warnings.append(f"{n['metadata']['title'][:80]}: a review with no lineage in its "
                                "nucleus can't exist here, so it was skipped.")
                continue
            relevance = r.get("relevance")
            review.status = r["status"]
            review.rejection_reason = r.get("rejection_reason")
            review.selection_reasoning = r.get("selection_reasoning")
            review.relevance_text = relevance["text"] if relevance else None
            review.relevance_provenance = relevance["provenance"] if relevance else None
            if relevance and relevance.get("audit"):
                audited += 1
            for i, e in enumerate(r.get("evidence", [])):
                session.add(NodeEvidence(
                    id=uuid.uuid4(), review_id=review.id, ord=i, source=e["source"],
                    verbatim_text=e["selector"]["exact"], text_quote_selector=e["selector"],
                ))
    if audited:
        warnings.append(f"{audited} automatic audit verdict(s) were measured on the exporting "
                        "server and are not imported; the statements import unaudited.")

    # Semantic relations.
    now = datetime.now(UTC)
    for e in data.get("edges", []):
        if e["kind"] != "semantic":
            continue
        if e["from"] not in local_node or e["to"] not in local_node:
            warnings.append("A relation named a paper the file doesn't contain; it was skipped.")
            continue
        edge = SemanticEdge(
            id=uuid.uuid4(), project_id=project_id, from_node_id=local_node[e["from"]],
            to_node_id=local_node[e["to"]], relation=e["relation"],
            proposed_relation=e.get("proposed_relation"), provenance=e["provenance"],
            rationale=e.get("rationale"), rejected_at=now if e.get("rejected") else None,
            rejection_reason=e.get("rejection_reason"), created_by=user(e.get("created_by")),
        )
        if e.get("created"):
            edge.created_at = _dt(e["created"])  # type: ignore[assignment]
        session.add(edge)
        await session.flush()
        for i, x in enumerate(e.get("evidence", [])):
            session.add(SemanticEdgeEvidence(
                id=uuid.uuid4(), edge_id=edge.id, ord=i, source=x["source"],
                verbatim_text=x["selector"]["exact"], text_quote_selector=x["selector"],
            ))
    await session.flush()
    return warnings
