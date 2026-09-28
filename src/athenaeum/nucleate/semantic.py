"""Semantic relations between papers: proposing them, and reviewing them.

Proposing (D-N19) is on request, for one paper A at a time, and evidence comes
first. A's own sentences are searched, deterministically, for each other
paper B's first-author surname and year: the places A cites B. Only those
sentences go to the archivist, which names a relation from the six (or none)
and picks the sentence that shows it by number. The stored quote is that
sentence, verbatim, anchored in A's text; the model never writes the quote.
When nothing mentions B, no model call is made for it at all.

Reviewing follows the node-review rules (nucleate/review.py): accepting an
AI relation as proposed makes it ai_proposed_accepted; changing it makes it
ai_proposed_corrected, or accepted again if changed back to the proposal; a
rejection keeps the row, hidden, as steering signal.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain import nucleate as v
from athenaeum.domain.relations import SEMANTIC_RELATIONS
from athenaeum.domain.source_check import load_paper_sentences, text_quote_selector
from athenaeum.nucleate.retrieval import author_names
from athenaeum.provider.base import Message, Provider
from athenaeum.storage.models import CorpusNode, Paper, SemanticEdge, SemanticEdgeEvidence
from athenaeum.synthesize.prompts import ARCHIVIST_RELATIONS_SYSTEM, archivist_relations_tool

log = structlog.get_logger()

# Per paper B, how many citing sentences the archivist sees. A paper cited
# more often than this is still judged on its first mentions.
MAX_MENTIONS_PER_PAPER = 4

# Outcomes per paper B, reported back to the drawer so "nothing proposed"
# always says why. Response labels only; nothing stores them.
PROPOSED = "proposed"
NO_FULL_TEXT = "no_full_text"
NO_MENTION = "no_mention"
NO_RELATION = "no_relation"
ALREADY_RECORDED = "already_recorded"


# ── Finding where A cites B ──────────────────────────────────────────────────


def _fold(text: str) -> str:
    """Accents off and compatibility forms folded, so "Müller" finds "Muller"
    and the reverse. Case is kept: surnames are capitalised in citations, and
    matching lowercase would turn names like Green or Young into words."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def first_author_surname(paper: Paper) -> str | None:
    """The first author's surname, from "Given Family" or "Family, Given"."""
    names = author_names(paper.authors_json)
    if not names:
        return None
    name = names[0].strip()
    if "," in name:
        surname = name.split(",", 1)[0].strip()
    else:
        parts = [p for p in name.split() if p.rstrip(".").lower() not in ("jr", "sr", "ii", "iii")]
        surname = parts[-1] if parts else ""
    # An initial alone ("B.") can't identify anyone in a citation.
    return surname if len(surname.rstrip(".")) > 1 else None


def find_mentions(
    sentences: Sequence[tuple[uuid.UUID, str]], surname: str, year: int | None
) -> list[tuple[uuid.UUID, str]]:
    """A's sentences naming B: B's surname as a whole word and, when B's year
    is known, the year (or 2018a-style) in the same sentence."""
    name = re.compile(rf"(?<!\w){re.escape(_fold(surname))}(?!\w)")
    when = re.compile(rf"(?<!\d){year}[a-z]?(?!\d)") if year else None
    found = []
    for sid, text in sentences:
        folded = _fold(text)
        if name.search(folded) and (when is None or when.search(folded)):
            found.append((sid, text))
    return found


# ── Proposing ────────────────────────────────────────────────────────────────


@dataclass
class PairOutcome:
    to_node_id: uuid.UUID
    outcome: str
    proposed: int = 0


@dataclass
class ProposalResult:
    edges: list[SemanticEdge] = field(default_factory=list)
    outcomes: list[PairOutcome] = field(default_factory=list)


def _label(paper: Paper) -> str:
    names = author_names(paper.authors_json)
    first = names[0] if names else "Unknown author"
    etal = " et al." if len(names) > 1 else ""
    return f"{first}{etal} ({paper.year or 'n.d.'}), \"{paper.title}\""


async def propose_relations(
    session: AsyncSession,
    provider: Provider,
    project_id: uuid.UUID,
    from_node: CorpusNode,
    others: Sequence[CorpusNode],
    user_id: uuid.UUID,
) -> ProposalResult:
    """Propose relations from `from_node`'s paper to each of `others`, and
    store them as ai_proposed edges with their evidence. Flushes, does not
    commit."""
    result = ProposalResult()
    a = await session.get(Paper, from_node.paper_id)
    assert a is not None
    if a.status != "full_text":
        result.outcomes = [PairOutcome(n.id, NO_FULL_TEXT) for n in others]
        return result

    sentences = await load_paper_sentences(session, a.id)
    recorded = {
        (to, rel) for to, rel in (await session.execute(
            select(SemanticEdge.to_node_id, SemanticEdge.relation)
            .where(SemanticEdge.from_node_id == from_node.id)
        )).tuples()
    }

    # Evidence first: which of A's sentences cite each B. Numbered globally
    # so the archivist can only point at a sentence it was shown.
    papers = {p.id: p for p in await session.scalars(
        select(Paper).where(Paper.id.in_([n.paper_id for n in others])))}
    cited: list[tuple[CorpusNode, Paper, list[tuple[int, uuid.UUID, str]]]] = []
    numbered: dict[int, tuple[uuid.UUID, str]] = {}
    outcome: dict[uuid.UUID, PairOutcome] = {}
    for node in others:
        b = papers[node.paper_id]
        surname = first_author_surname(b)
        mentions = find_mentions(sentences, surname, b.year)[:MAX_MENTIONS_PER_PAPER] \
            if surname else []
        outcome[node.id] = PairOutcome(node.id, NO_MENTION if not mentions else NO_RELATION)
        if not mentions:
            continue
        entries = []
        for sid, text in mentions:
            n = len(numbered) + 1
            numbered[n] = (sid, text)
            entries.append((n, sid, text))
        cited.append((node, b, entries))
    result.outcomes = [outcome[n.id] for n in others]
    if not cited:
        return result

    lines = [f"PAPER A: {_label(a)}", ""]
    for i, (_node, b, entries) in enumerate(cited, start=1):
        lines.append(f"PAPER B{i}: {_label(b)}")
        lines += [f"  [{n}] {text}" for n, _sid, text in entries]
        lines.append("")
    lines.append("Give the relations as paper = the B number, sentence = the sentence number.")
    _, tool = await provider.complete(
        messages=[Message(role="user", content="\n".join(lines))],
        system=ARCHIVIST_RELATIONS_SYSTEM,
        max_tokens=2048,
        tools=[archivist_relations_tool()],
    )
    proposals: list[dict[str, Any]] = (tool or {}).get("input", {}).get("relations") or []

    for p in proposals:
        index, sentence = p.get("paper"), p.get("sentence")
        if not isinstance(index, int) or not isinstance(sentence, int):
            continue
        relation = p.get("relation")
        if not (1 <= index <= len(cited)) or relation not in SEMANTIC_RELATIONS:
            continue
        node, _b, entries = cited[index - 1]
        # The sentence must be one shown for this B: a relation can't borrow
        # another paper's evidence.
        chosen = next(((sid, text) for n, sid, text in entries if n == sentence), None)
        if chosen is None:
            log.warning("semantic.proposal_sentence_mismatch", paper=index, sentence=sentence)
            continue
        if (node.id, relation) in recorded:
            if outcome[node.id].proposed == 0:
                outcome[node.id].outcome = ALREADY_RECORDED
            continue
        sid, text = chosen
        selector = text_quote_selector(text, sentences, frozenset({sid}))
        if selector is None:
            continue
        edge = SemanticEdge(
            id=uuid.uuid4(), project_id=project_id, from_node_id=from_node.id,
            to_node_id=node.id, relation=relation, proposed_relation=relation,
            provenance=v.AI_PROPOSED,
            rationale=str(p.get("rationale") or "").strip()[:500] or None,
            created_by=user_id,
        )
        session.add(edge)
        await session.flush()
        session.add(SemanticEdgeEvidence(
            id=uuid.uuid4(), edge_id=edge.id, ord=0, source=v.FULL_TEXT,
            verbatim_text=text, text_quote_selector=selector,
        ))
        recorded.add((node.id, relation))
        result.edges.append(edge)
        outcome[node.id].outcome = PROPOSED
        outcome[node.id].proposed += 1
    await session.flush()
    return result


# ── Reviewing ────────────────────────────────────────────────────────────────


class InvalidEdgeUpdate(ValueError):
    pass


ACCEPT = "accept"
CORRECT = "correct"
REJECT = "reject"
RESTORE = "restore"
EDGE_ACTIONS = (ACCEPT, CORRECT, REJECT, RESTORE)


def apply_edge_update(
    edge: SemanticEdge, action: str, *, relation: str | None = None,
    rejection_reason: str | None = None,
) -> None:
    """One action from the drawer, applied in place.

    accept   an AI relation as proposed (clearing any rejection)
    correct  to another relation; the user's own relation just changes
    reject   an AI relation, with an optional reason; the row stays
    restore  a rejected relation, to what it was before
    """
    ai = edge.provenance != v.USER_SET
    if action == ACCEPT:
        if not ai:
            raise InvalidEdgeUpdate("a relation you set yourself has nothing to accept")
        edge.relation = edge.proposed_relation or edge.relation
        edge.provenance = v.AI_PROPOSED_ACCEPTED
        edge.rejected_at = None
        edge.rejection_reason = None
    elif action == CORRECT:
        if relation not in SEMANTIC_RELATIONS:
            raise InvalidEdgeUpdate(f"unknown relation {relation!r}")
        edge.relation = relation
        if ai:
            edge.provenance = (v.AI_PROPOSED_ACCEPTED if relation == edge.proposed_relation
                               else v.AI_PROPOSED_CORRECTED)
        edge.rejected_at = None
        edge.rejection_reason = None
    elif action == REJECT:
        if not ai:
            raise InvalidEdgeUpdate("delete a relation you set yourself rather than rejecting it")
        edge.rejected_at = datetime.now(UTC)
        edge.rejection_reason = (rejection_reason or "").strip() or None
    elif action == RESTORE:
        edge.rejected_at = None
        edge.rejection_reason = None
    else:
        raise InvalidEdgeUpdate(f"unknown action {action!r}")
