"""Tests for the verdict taxonomy's consistency and for today's Audit as a predictor.

Verdict names live in two places, Python and a database CHECK constraint. The
consistency tests fail if those ever drift apart, which is how a verdict could
silently become unstorable, or storable but unknown to the evaluation harness.
"""
from __future__ import annotations

import re

from athenaeum.domain.audit_verdicts import (
    BAD_VERDICTS,
    DEFINITIONS,
    DIFFERS_FROM_SOURCE,
    JUDGMENT_VERDICTS,
    SUPPORTED,
    VERDICTS,
)
from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.predictors import NOT_SUPPORTED, BinaryAuditPredictor, binary_spec
from athenaeum.evaluation.runner import PREDICTION_ERROR
from athenaeum.provider.base import Message
from athenaeum.storage.models import AuditFinding

# ── Taxonomy ─────────────────────────────────────────────────────────────────

def test_every_verdict_has_a_definition() -> None:
    assert set(DEFINITIONS) == set(VERDICTS)


def test_misread_definition_names_every_failure_folded_into_it() -> None:
    """Folding 'doesn't establish it' and 'unrelated' into misread only works
    if its definition says so; otherwise labelers won't know where those go."""
    text = DEFINITIONS["misread"]

    for phrase in ("materially different", "contradicts", "without establishing", "unrelated"):
        assert phrase in text


def test_deterministic_verdict_is_kept_out_of_judgment_but_counts_as_bad() -> None:
    assert DIFFERS_FROM_SOURCE not in JUDGMENT_VERDICTS
    assert DIFFERS_FROM_SOURCE in BAD_VERDICTS
    assert set(JUDGMENT_VERDICTS) | {DIFFERS_FROM_SOURCE} == set(VERDICTS)
    assert SUPPORTED not in BAD_VERDICTS


def test_database_constraint_allows_exactly_the_verdicts() -> None:
    constraint = next(
        c for c in AuditFinding.__table__.constraints
        if c.name == "audit_findings_verdict_chk"
    )

    allowed = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))

    assert allowed == set(VERDICTS)


# ── Today's Audit as a predictor ─────────────────────────────────────────────

class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        return None, self._tool_result


def _results(*supported: bool) -> dict:
    return {
        "name": "audit_evidence",
        "input": {"results": [
            {"evidence_index": i, "supported": s, "rationale": f"r{i}"}
            for i, s in enumerate(supported)
        ]},
    }


def _item(*quotes: str) -> EvalItem:
    return EvalItem(
        id="a1", claim="A claim.", quotes=quotes, true_verdict="supported", source="set-a1",
    )


async def test_binary_predictor_maps_pass_fail_and_keeps_the_rationale() -> None:
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {"results": [
            {"evidence_index": 0, "supported": False, "rationale": "Broader than the quote."},
        ]},
    })
    predictor = BinaryAuditPredictor(provider, "model-x")
    item = EvalItem(
        id="a1", claim="Warming raises efflux everywhere.", quotes=("Efflux rose in plot A.",),
        true_verdict="overreach", source="set-a1",
    )

    prediction = await predictor.predict(item)

    assert (prediction.item_id, prediction.verdict) == ("a1", NOT_SUPPORTED)
    assert prediction.rationale == "Broader than the quote."
    assert predictor.name == "audit-binary:model-x"
    assert provider.last_messages is not None
    sent = provider.last_messages[0].content
    assert "Warming raises efflux everywhere." in sent
    assert "Efflux rose in plot A." in sent


async def test_binary_predictor_calls_a_pass_supported() -> None:
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {"results": [{"evidence_index": 0, "supported": True, "rationale": "Direct."}]},
    })

    prediction = await BinaryAuditPredictor(provider, "m").predict(
        EvalItem(id="a2", claim="C.", quotes=("Q.",), true_verdict="supported", source="set-a1")
    )

    assert prediction.verdict == SUPPORTED


async def test_a_multi_quote_item_is_supported_only_when_every_quote_is() -> None:
    """A rationale set is evidence only as a whole, so one rejected quote makes
    the item not supported; the per-quote verdicts stay visible."""
    all_supported = BinaryAuditPredictor(_FakeProvider(_results(True, True)), "m")
    one_rejected = BinaryAuditPredictor(_FakeProvider(_results(True, False)), "m")
    item = _item("First sentence.", "Second sentence.")

    passed = await all_supported.predict(item)
    failed = await one_rejected.predict(item)

    assert passed.verdict == SUPPORTED
    assert passed.quote_verdicts == (SUPPORTED, SUPPORTED)
    assert failed.verdict == NOT_SUPPORTED
    assert failed.quote_verdicts == (SUPPORTED, NOT_SUPPORTED)
    assert "r0" in failed.rationale and "r1" in failed.rationale


async def test_the_predictor_accumulates_response_integrity_across_items() -> None:
    """One run's skip rate is the sum over its items; without it, a run the
    model answered badly scores the same as one it answered well."""
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {"results": [
            "not an object",
            {"evidence_index": 1, "supported": True, "rationale": "Fine."},
        ]},
    })
    predictor = BinaryAuditPredictor(provider, "m")

    await predictor.predict(_item("First.", "Second."))
    await predictor.predict(_item("Third.", "Fourth."))

    assert predictor.quotes_checked == 4
    assert predictor.malformed_entries == 2
    assert predictor.missing_verdicts == 2


async def test_a_clean_run_leaves_the_counters_at_zero() -> None:
    predictor = BinaryAuditPredictor(_FakeProvider(_results(True, True)), "m")

    await predictor.predict(_item("First.", "Second."))

    assert (predictor.malformed_entries, predictor.missing_verdicts) == (0, 0)
    assert predictor.quotes_checked == 2


def test_binary_spec_scores_against_every_graded_verdict() -> None:
    spec = binary_spec()

    assert spec.true_labels == VERDICTS
    # `error` is a real outcome of a run: a predictor that fails on an item
    # is recorded rather than dropped, so the matrix has a column for it.
    assert spec.predicted_labels == (SUPPORTED, NOT_SUPPORTED, PREDICTION_ERROR)
    assert spec.safe_prediction == SUPPORTED
