"""Tests for the corpus edge relation vocabularies.

These are consistency tests, in the same spirit as tests/test_audit_verdicts_
and_predictors.py: a relation name lives in Python, in a JSON-LD context, and
(once semantic edges have a table) in a database CHECK constraint, and the
tests fail if those drift apart. They also pin the two decisions that are easy
to erode later — that there is no catch-all relation, and that the question-side
vocabulary does not borrow CiTO terms.
"""
from __future__ import annotations

import re

from athenaeum.domain.relations import (
    ATH_NS,
    CITO_NS,
    DISAGREES_WITH,
    EXTENDS,
    LEGACY_KNOWLEDGE_LINK_RELS,
    QUESTION_DEFINITIONS,
    QUESTION_IRIS,
    QUESTION_RELATIONS,
    SEMANTIC_DEFINITIONS,
    SEMANTIC_IRIS,
    SEMANTIC_RELATIONS,
    SUPPORTS,
    jsonld_context,
)
from athenaeum.storage.models import KnowledgeLink

# ── Both vocabularies are complete ───────────────────────────────────────────

def test_every_semantic_relation_has_a_definition_and_an_iri() -> None:
    assert set(SEMANTIC_DEFINITIONS) == set(SEMANTIC_RELATIONS)
    assert set(SEMANTIC_IRIS) == set(SEMANTIC_RELATIONS)


def test_every_question_relation_has_a_definition_and_an_iri() -> None:
    assert set(QUESTION_DEFINITIONS) == set(QUESTION_RELATIONS)
    assert set(QUESTION_IRIS) == set(QUESTION_RELATIONS)


def test_semantic_relations_map_into_cito_and_question_relations_do_not() -> None:
    """The separation is the decision: paper-to-paper edges reuse CiTO, edges
    leaving a nucleus do not, because a nucleus is a question, not a document."""
    assert all(iri.startswith(CITO_NS) for iri in SEMANTIC_IRIS.values())
    assert all(iri.startswith(ATH_NS) for iri in QUESTION_IRIS.values())


def test_the_two_vocabularies_share_no_term_names() -> None:
    """`uses_method_of` and `provides_method_for` describe the same underlying
    fact from opposite ends; one name meaning both would make edges ambiguous."""
    assert not set(SEMANTIC_RELATIONS) & set(QUESTION_RELATIONS)


# ── The decisions that are easy to erode ─────────────────────────────────────

def test_there_is_no_catch_all_relation() -> None:
    """A 'related' bucket absorbs every uncertain call and makes the specific
    relations meaningless. Absence of a semantic edge is the representation."""
    for catch_all in ("related", "cites", "cites_as_related", "other", "unknown"):
        assert catch_all not in SEMANTIC_RELATIONS
        assert catch_all not in QUESTION_RELATIONS


def test_supports_is_defined_without_leaning_on_disagrees_with() -> None:
    """cito:supports is rhetorical endorsement, not 'results replicate'. Its
    definition has to say so, or a labeler reads it as the negation of
    disagrees_with and the two stop being independently decidable."""
    text = SEMANTIC_DEFINITIONS[SUPPORTS]

    assert "endorses" in text
    assert "not" in text and "replicate" in text
    assert DISAGREES_WITH not in text


def test_semantic_relations_stay_at_six_terms() -> None:
    """Six is a measured trade-off, not an accident: three-class citation-intent
    schemes score ~89% macro-F1 and six-class ones ~76%, and six is where
    agreement and conflict stay distinguishable. Growing the set silently is
    how the ~76% figure quietly stops applying."""
    assert len(SEMANTIC_RELATIONS) == 6


# ── Legacy mapping ───────────────────────────────────────────────────────────

def test_legacy_map_covers_exactly_the_values_knowledge_link_can_store() -> None:
    """KnowledgeLink.rel's CHECK constraint is the authority on what rows exist.
    A value missing from the map is a row the migration would not know how to
    handle."""
    constraint = next(
        c for c in KnowledgeLink.__table__.constraints
        if c.name == "knowledge_links_rel_chk"
    )

    allowed = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))

    assert allowed == set(LEGACY_KNOWLEDGE_LINK_RELS)


def test_legacy_catch_alls_map_to_nothing_and_the_rest_map_into_the_new_set() -> None:
    assert LEGACY_KNOWLEDGE_LINK_RELS["cites"] is None
    assert LEGACY_KNOWLEDGE_LINK_RELS["related"] is None

    assert LEGACY_KNOWLEDGE_LINK_RELS["supports"] == SUPPORTS
    assert LEGACY_KNOWLEDGE_LINK_RELS["contradicts"] == DISAGREES_WITH
    assert LEGACY_KNOWLEDGE_LINK_RELS["extends"] == EXTENDS

    mapped = {v for v in LEGACY_KNOWLEDGE_LINK_RELS.values() if v is not None}
    assert mapped <= set(SEMANTIC_RELATIONS)


# ── JSON-LD context ──────────────────────────────────────────────────────────

def test_context_resolves_every_relation_term() -> None:
    context = jsonld_context()

    for relation in (*SEMANTIC_RELATIONS, *QUESTION_RELATIONS):
        assert relation in context

    assert context["cito"] == CITO_NS
    assert context["ath"] == ATH_NS


def test_context_is_a_fresh_dict_each_call() -> None:
    """Callers build an export document around it; a shared mutable constant
    would let one export's edits leak into the next."""
    first = jsonld_context()
    first["cito"] = "tampered"

    assert jsonld_context()["cito"] == CITO_NS
