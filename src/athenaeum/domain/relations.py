"""Relation vocabularies for corpus edges: names, definitions, and IRIs.

Kept in one module for the same reason as domain/audit_verdicts.py — the
archivist's prompt, the API, the export and any future labeling guide have to
use the same words with the same meanings. Two vocabularies live here, and they
are deliberately not merged:

- **Semantic relations** type a paper→paper edge and map onto CiTO, the
  Citation Typing Ontology (``http://purl.org/spar/cito/``).
- **Question relations** type a nucleus→paper edge. A nucleus is a question,
  not a document, so these sit in Athenaeum's own namespace rather than
  borrowing CiTO terms for a role they were not written for. CiTO declares no
  domain or range on any property, so using its terms here would have been
  formally valid; the separation is for readers, not for the schema.

Why the semantic set is six terms (decided 2026-09-26). CiTO lists 44
sub-properties of ``cito:cites``, each with an inverse. Evidence that the full
set is too large to apply consistently: annotators' mental models "hardly ever
converge to a single shared opinion" (Evaluating citation functions in CiTO:
cognitive issues, ESWC 2014), and the *Journal of Cheminformatics* CiTO pilot
(2020-2022) was discontinued after 15 articles, principally over author burden.
Meanwhile citation-intent classifiers reach roughly 89% macro-F1 on a
three-class scheme (SciCite) and roughly 76% on a six-class one (ACL-ARC). Six
was chosen over three because three collapses agreement and conflict into one
"result" class, and telling those apart is the whole point of an archivist that
is allowed to observe "these two papers report growth rates an order of
magnitude apart".

There is deliberately **no catch-all relation**. CiTO's ``citesAsRelated`` and
plain ``cites`` are left out: a "related" bucket is where every uncertain call
lands, which would make the other six meaningless. When the archivist cannot
name one of the six it proposes no semantic edge at all, and nothing is lost,
because the lineage edge already records that one paper was found from another.

Edge provenance values (ai_proposed_accepted and friends) are not here; they
belong with the node schema, which records provenance on statements too.
"""
from __future__ import annotations

from typing import Final, Literal

# Vocabulary namespaces, used to build the export's single JSON-LD @context.
# The Athenaeum namespace is a placeholder until the context document is
# actually published, matching the handoff's own placeholder for it.
CITO_NS: Final = "http://purl.org/spar/cito/"
ATH_NS: Final = "https://athenaeum.example/vocab/"


# ── Semantic relations: paper → paper ────────────────────────────────────────
#
# Every one reads A → B with A as the citing or using paper, so direction never
# has to be reasoned about per relation. Each maps to exactly one CiTO IRI.

USES_METHOD_OF: Final = "uses_method_of"
USES_DATA_FROM: Final = "uses_data_from"
EXTENDS: Final = "extends"
SUPPORTS: Final = "supports"
DISAGREES_WITH: Final = "disagrees_with"
OBTAINS_BACKGROUND_FROM: Final = "obtains_background_from"

SemanticRelation = Literal[
    "uses_method_of",
    "uses_data_from",
    "extends",
    "supports",
    "disagrees_with",
    "obtains_background_from",
]

SEMANTIC_RELATIONS: tuple[SemanticRelation, ...] = (
    USES_METHOD_OF,
    USES_DATA_FROM,
    EXTENDS,
    SUPPORTS,
    DISAGREES_WITH,
    OBTAINS_BACKGROUND_FROM,
)

SEMANTIC_DEFINITIONS: dict[SemanticRelation, str] = {
    USES_METHOD_OF: (
        "A applies a method, protocol, instrument, or software introduced or described "
        "in B."
    ),
    USES_DATA_FROM: "A reuses data that B produced or published.",
    EXTENDS: (
        "A builds directly on B and goes beyond it — a further step in the same line of "
        "work, not merely agreement with it."
    ),
    SUPPORTS: (
        "A provides intellectual support for B: it endorses or argues for B's position "
        "or findings. This is CiTO's rhetorical sense of support, not 'A's results "
        "replicate B's'."
    ),
    DISAGREES_WITH: "A's claims or results conflict with B's.",
    OBTAINS_BACKGROUND_FROM: (
        "A relies on B for context or prior knowledge, without using B's methods, data, "
        "or results as such."
    ),
}

# `supports` and `disagrees_with` are NOT opposites, and the archivist's prompt
# must define each on its own terms rather than as the negation of the other.
# cito:supports is about A endorsing B; cito:disagreesWith is about A's claims
# conflicting with B's. A paper can do neither, and a paper that fails to
# replicate B disagrees with it without ever having "supported" anything.
# `supports` was chosen over the tighter cito:confirms ("A's results confirm
# B's") on 2026-09-26 to keep continuity with KnowledgeLink's existing value.
SUPPORTS_ASYMMETRY_NOTE: Final = (
    "cito:supports is rhetorical endorsement by A of B; cito:disagreesWith is a "
    "conflict between A's and B's claims. They are not inverses, and neither is the "
    "default when the other does not hold."
)

SEMANTIC_IRIS: dict[SemanticRelation, str] = {
    USES_METHOD_OF: f"{CITO_NS}usesMethodIn",
    USES_DATA_FROM: f"{CITO_NS}usesDataFrom",
    EXTENDS: f"{CITO_NS}extends",
    SUPPORTS: f"{CITO_NS}supports",
    DISAGREES_WITH: f"{CITO_NS}disagreesWith",
    OBTAINS_BACKGROUND_FROM: f"{CITO_NS}obtainsBackgroundFrom",
}


# ── Question relations: nucleus → paper ──────────────────────────────────────
#
# Read nucleus → paper. "Supports" is meaningless on an edge leaving a
# question, which is why these exist at all rather than reusing the set above.

ADDRESSES: Final = "addresses"
MEASURES: Final = "measures"
PROVIDES_METHOD_FOR: Final = "provides_method_for"
PROVIDES_CONTEXT_FOR: Final = "provides_context_for"

QuestionRelation = Literal[
    "addresses",
    "measures",
    "provides_method_for",
    "provides_context_for",
]

QUESTION_RELATIONS: tuple[QuestionRelation, ...] = (
    ADDRESSES,
    MEASURES,
    PROVIDES_METHOD_FOR,
    PROVIDES_CONTEXT_FOR,
)

QUESTION_DEFINITIONS: dict[QuestionRelation, str] = {
    ADDRESSES: (
        "The paper takes up the nucleus's question directly, as a question it sets out "
        "to answer."
    ),
    MEASURES: "The paper reports a measurement of the quantity the nucleus asks about.",
    PROVIDES_METHOD_FOR: (
        "The paper supplies a method usable for answering the nucleus's question, "
        "without necessarily answering it."
    ),
    PROVIDES_CONTEXT_FOR: (
        "The paper gives background the question depends on, without addressing or "
        "measuring it."
    ),
}

QUESTION_IRIS: dict[QuestionRelation, str] = {
    ADDRESSES: f"{ATH_NS}addresses",
    MEASURES: f"{ATH_NS}measures",
    PROVIDES_METHOD_FOR: f"{ATH_NS}providesMethodFor",
    PROVIDES_CONTEXT_FOR: f"{ATH_NS}providesContextFor",
}


# ── Legacy: KnowledgeLink.rel ───────────────────────────────────────────────
#
# KnowledgeLink is the older of the two relation vocabularies this module
# replaces (the other, StatementEdge.relation, is retired with the statement
# graph and has no forward mapping — its edges were user-drawn between
# statements, not papers). Nothing in the frontend ever consumed KnowledgeLink,
# so this mapping exists for the rows alone. None means "no semantic edge":
# those values are catch-alls with no equivalent in the six-term set, and the
# migration that applies this map reports their row counts rather than guessing.

LEGACY_KNOWLEDGE_LINK_RELS: dict[str, SemanticRelation | None] = {
    "supports": SUPPORTS,
    "contradicts": DISAGREES_WITH,
    "extends": EXTENDS,
    "cites": None,
    "related": None,
}


# ── JSON-LD ─────────────────────────────────────────────────────────────────

def jsonld_context() -> dict[str, str]:
    """The vocabulary half of the export's single `@context`.

    Emits the two namespace prefixes plus one entry per relation term, so a
    consumer that understands JSON-LD resolves every relation to a real IRI and
    one that does not still reads the file as plain JSON. Kept as a function
    rather than a constant so callers cannot mutate the shared dict.
    """
    context: dict[str, str] = {"cito": CITO_NS, "ath": ATH_NS}
    for relation, iri in SEMANTIC_IRIS.items():
        context[relation] = iri
    for question_relation, question_iri in QUESTION_IRIS.items():
        context[question_relation] = question_iri
    return context
