"""Tests for the graded, set-level Audit variant used by the evaluation harness.

Two properties matter most here and are pinned down below: a failed call is
never recorded as a judgment (`indeterminate` means the model said it couldn't
tell, not that the call broke), and the item-level verdict is the one the model
gave for the whole set, with no rollup rule invented after the fact.
"""
from __future__ import annotations

import pytest

from athenaeum.domain.audit_verdicts import (
    DIFFERS_FROM_SOURCE,
    INDETERMINATE,
    JUDGMENT_VERDICTS,
    MISREAD,
    OVERREACH,
    SUPPORTED,
    VERDICTS,
)
from athenaeum.evaluation.graded import (
    GRADED_AUDIT_SYSTEM,
    GRADED_AUDIT_TOOL,
    NO_VERDICT,
    GradedAuditPredictor,
    audit_evidence_graded,
    graded_spec,
)
from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.runner import PREDICTION_ERROR
from athenaeum.provider.base import Message


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.calls = 0
        self.last_messages: list[Message] | None = None
        self.last_system: str | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.calls += 1
        self.last_messages = messages
        self.last_system = system
        return None, self._tool_result


def _answer(**payload) -> dict:
    return {"name": "audit_evidence_graded", "input": payload}


def _item(*quotes: str, verdict: str = SUPPORTED) -> EvalItem:
    return EvalItem(
        id="a1", claim="A claim.", quotes=quotes, true_verdict=verdict, source="set-a1",
    )


# ── Prompt and tool ──────────────────────────────────────────────────────────

def test_the_prompt_offers_every_verdict_a_model_decides() -> None:
    for verdict in JUDGMENT_VERDICTS:
        assert verdict in GRADED_AUDIT_SYSTEM


def test_the_deterministic_verdict_is_not_offered_to_the_model() -> None:
    """Whether a quote matches the cited paper is the source check's call."""
    schema = GRADED_AUDIT_TOOL["input_schema"]["properties"]

    assert DIFFERS_FROM_SOURCE not in schema["set_verdict"]["enum"]
    assert DIFFERS_FROM_SOURCE not in GRADED_AUDIT_SYSTEM


def test_the_tool_asks_for_a_set_verdict_and_per_quote_verdicts() -> None:
    schema = GRADED_AUDIT_TOOL["input_schema"]

    assert "set_verdict" in schema["required"]
    assert schema["properties"]["per_quote"]["items"]["required"] == [
        "evidence_index", "verdict", "rationale",
    ]


# ── Reading the answer ───────────────────────────────────────────────────────

async def test_a_full_answer_is_read_back_whole() -> None:
    provider = _FakeProvider(_answer(
        set_verdict=OVERREACH,
        set_rationale="The claim is broader than the two sentences together.",
        secondhand=False,
        confidence=0.8,
        per_quote=[
            {"evidence_index": 0, "verdict": SUPPORTED, "rationale": "Direct."},
            {"evidence_index": 1, "verdict": OVERREACH, "rationale": "Narrower population."},
        ],
    ))

    graded = await audit_evidence_graded(provider, "A claim.", ["First.", "Second."], "m")

    assert graded.set_verdict == OVERREACH
    assert graded.quote_verdicts == (SUPPORTED, OVERREACH)
    assert (graded.secondhand, graded.confidence) == (False, 0.8)
    assert (graded.malformed_entries, graded.missing_verdicts) == (0, 0)


async def test_one_call_carries_every_quote_in_the_set() -> None:
    """The set is judged as a set: one call, all quotes, no rollup."""
    provider = _FakeProvider(_answer(
        set_verdict=SUPPORTED, set_rationale="Together they state it.",
        per_quote=[{"evidence_index": i, "verdict": SUPPORTED, "rationale": "x"} for i in (0, 1)],
    ))

    await GradedAuditPredictor(provider, "m").predict(_item("First.", "Second."))

    assert provider.calls == 1
    assert provider.last_messages is not None
    sent = provider.last_messages[0].content
    assert "First." in sent and "Second." in sent


async def test_a_missing_set_verdict_is_an_error_not_an_indeterminate_judgment() -> None:
    """`indeterminate` means the model said it couldn't tell. A call that
    returned nothing usable didn't say anything, and scoring the two as the
    same thing would credit a broken call as a judgment."""
    provider = _FakeProvider(_answer(set_rationale="...", per_quote=[]))

    prediction = await GradedAuditPredictor(provider, "m").predict(_item("First."))

    assert prediction.verdict == PREDICTION_ERROR
    assert prediction.verdict != INDETERMINATE


async def test_a_single_quote_item_takes_its_set_verdict_from_that_quote() -> None:
    """Observed on a real run: the model answers the per-quote question fully
    and omits the set field. With one quote the two questions are the same
    question, so reading one off the other is an identity, not a rollup."""
    provider = _FakeProvider(_answer(
        set_rationale="",
        per_quote=[{"evidence_index": 0, "verdict": OVERREACH, "rationale": "Broader claim."}],
    ))

    graded = await audit_evidence_graded(provider, "A claim.", ["Only quote."], "m")

    assert graded.set_verdict == OVERREACH
    assert graded.recovered_set_verdict == 1


async def test_a_multi_quote_item_with_no_set_verdict_stays_an_error() -> None:
    """Combining two per-quote verdicts into a set verdict would invent the
    answer the model was asked for and didn't give."""
    provider = _FakeProvider(_answer(
        set_rationale="",
        per_quote=[
            {"evidence_index": 0, "verdict": SUPPORTED, "rationale": "x"},
            {"evidence_index": 1, "verdict": SUPPORTED, "rationale": "y"},
        ],
    ))

    prediction = await GradedAuditPredictor(provider, "m").predict(_item("First.", "Second."))

    assert prediction.verdict == PREDICTION_ERROR


async def test_recovery_needs_a_usable_quote_verdict() -> None:
    provider = _FakeProvider(_answer(set_rationale="", per_quote=[]))

    graded = await audit_evidence_graded(provider, "A claim.", ["Only quote."], "m")

    assert graded.set_verdict == NO_VERDICT
    assert (graded.missing_set_verdict, graded.recovered_set_verdict) == (1, 0)


async def test_a_verdict_outside_the_taxonomy_is_refused() -> None:
    provider = _FakeProvider(_answer(
        set_verdict="fabricated", set_rationale="...", per_quote=[],
    ))

    graded = await audit_evidence_graded(provider, "A claim.", ["First."], "m")

    assert graded.set_verdict == NO_VERDICT
    assert graded.missing_set_verdict == 1


async def test_a_missing_set_verdict_is_not_counted_against_the_quotes() -> None:
    """The two failures have different denominators. Adding a missing item
    field to a per-quote entry count once produced a rate over quotes that
    described neither failure, and tripped a run threshold on it."""
    provider = _FakeProvider(_answer(
        set_rationale="...",
        per_quote=[{"evidence_index": 0, "verdict": MISREAD, "rationale": "x"}],
    ))

    graded = await audit_evidence_graded(provider, "A claim.", ["First."], "m")

    assert graded.malformed_entries == 0
    assert (graded.missing_set_verdict, graded.recovered_set_verdict) == (1, 1)


async def test_a_recovered_item_is_named_in_the_predictors_record() -> None:
    """A verdict that came from the recovery rule has to be traceable to the
    item, not just present in a total."""
    provider = _FakeProvider(_answer(
        set_rationale="",
        per_quote=[{"evidence_index": 0, "verdict": MISREAD, "rationale": "x"}],
    ))
    predictor = GradedAuditPredictor(provider, "m")

    await predictor.predict(_item("Only quote."))

    assert predictor.recovered == {"a1": True}
    assert (predictor.missing_set_verdicts, predictor.recovered_set_verdicts) == (1, 1)


async def test_a_per_quote_entry_with_an_unknown_verdict_is_counted_and_dropped() -> None:
    provider = _FakeProvider(_answer(
        set_verdict=MISREAD, set_rationale="...",
        per_quote=[{"evidence_index": 0, "verdict": "wrong", "rationale": "x"}],
    ))

    graded = await audit_evidence_graded(provider, "A claim.", ["First."], "m")

    assert graded.set_verdict == MISREAD
    assert graded.quote_verdicts == (NO_VERDICT,)
    assert (graded.malformed_entries, graded.missing_verdicts) == (1, 1)


async def test_a_repeated_quote_index_keeps_the_first_verdict() -> None:
    provider = _FakeProvider(_answer(
        set_verdict=SUPPORTED, set_rationale="...",
        per_quote=[
            {"evidence_index": 0, "verdict": SUPPORTED, "rationale": "First."},
            {"evidence_index": 0, "verdict": MISREAD, "rationale": "Second."},
        ],
    ))

    graded = await audit_evidence_graded(provider, "A claim.", ["First."], "m")

    assert graded.quote_verdicts == (SUPPORTED,)
    assert graded.malformed_entries == 1


async def test_no_response_leaves_every_quote_without_a_verdict() -> None:
    graded = await audit_evidence_graded(_FakeProvider(None), "A claim.", ["A.", "B."], "m")

    assert graded.set_verdict == NO_VERDICT
    assert graded.quote_verdicts == (NO_VERDICT, NO_VERDICT)
    assert graded.missing_verdicts == 2


async def test_the_predictor_accumulates_response_integrity() -> None:
    provider = _FakeProvider(_answer(
        set_verdict=SUPPORTED, set_rationale="...",
        per_quote=[{"evidence_index": 0, "verdict": SUPPORTED, "rationale": "x"}],
    ))
    predictor = GradedAuditPredictor(provider, "m")

    await predictor.predict(_item("First.", "Second."))

    assert predictor.quotes_checked == 2
    assert predictor.missing_verdicts == 1


# ── Scoring ──────────────────────────────────────────────────────────────────

def test_graded_spec_can_predict_every_judgment_verdict() -> None:
    spec = graded_spec()

    assert spec.true_labels == VERDICTS
    assert spec.predicted_labels == (*JUDGMENT_VERDICTS, PREDICTION_ERROR)
    # Macro-F1 is defined only when the predictor can emit every judged verdict.
    assert set(spec.judgment_labels) <= set(spec.predicted_labels)


def test_the_deterministic_verdict_stays_a_true_label_it_cannot_predict() -> None:
    spec = graded_spec()

    assert DIFFERS_FROM_SOURCE in spec.true_labels
    assert DIFFERS_FROM_SOURCE not in spec.predicted_labels
    assert DIFFERS_FROM_SOURCE not in spec.judgment_labels


@pytest.mark.parametrize("verdict", [SUPPORTED, MISREAD, OVERREACH, INDETERMINATE])
async def test_every_offered_verdict_survives_the_round_trip(verdict: str) -> None:
    provider = _FakeProvider(_answer(set_verdict=verdict, set_rationale="r", per_quote=[]))

    prediction = await GradedAuditPredictor(provider, "m").predict(_item("First."))

    assert prediction.verdict == verdict
