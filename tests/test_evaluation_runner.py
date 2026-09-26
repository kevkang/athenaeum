"""Tests for loading labeled sets and running and scoring a predictor.

The rules these pin down protect the honesty of a published number: a set's
file can't silently lose a label to a typo, sets are never pooled, every item
must be predicted, and the report presents evidence in a fixed order with no
summary verdict.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from athenaeum.evaluation.items import EvalItem, load_items, parse_item
from athenaeum.evaluation.runner import (
    PREDICTION_ERROR,
    Prediction,
    ScoringSpec,
    render_markdown,
    run_predictor,
    score,
)

GRADED = ("supported", "overreach", "misread")

SPEC_GRADED = ScoringSpec(
    true_labels=GRADED,
    predicted_labels=GRADED,
    judgment_labels=GRADED,
    bad_labels=("overreach", "misread"),
    safe_prediction="supported",
)

SPEC_BINARY = ScoringSpec(
    true_labels=GRADED,
    predicted_labels=("supported", "not_supported"),
    judgment_labels=GRADED,
    bad_labels=("overreach", "misread"),
    safe_prediction="supported",
)


def _item(n: int, verdict: str, source: str = "set-a") -> EvalItem:
    return EvalItem(
        id=f"i{n}", claim="A claim.", quotes=("A quote.",), true_verdict=verdict, source=source
    )


class _FakePredictor:
    """Answers from a fixed table, sleeping longest on the earliest items so
    that completion order is the reverse of input order."""

    name = "fake"

    def __init__(self, verdicts: dict[str, str]) -> None:
        self.verdicts = verdicts

    async def predict(self, item: EvalItem) -> Prediction:
        await asyncio.sleep(0.01 * (len(self.verdicts) - int(item.id[1:])))
        return Prediction(item.id, self.verdicts[item.id])


# ── Loading sets ─────────────────────────────────────────────────────────────

def _write(tmp_path: Path, *objects: dict) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text("\n".join(json.dumps(o) for o in objects) + "\n\n", encoding="utf-8")
    return path


VALID = {
    "id": "a1", "claim": "C.", "quotes": ["Q."], "true_verdict": "supported", "source": "set-a",
}


def test_load_items_reads_every_line_and_skips_blanks(tmp_path: Path) -> None:
    second = {**VALID, "id": "a2", "secondhand": True, "meta": {"scifact_id": 7}}
    path = _write(tmp_path, VALID, second)

    items = load_items(path)

    assert [i.id for i in items] == ["a1", "a2"]
    assert items[1].secondhand is True
    assert items[1].meta == {"scifact_id": 7}


def test_a_misspelled_field_fails_loudly_instead_of_dropping_the_label() -> None:
    bad = {k: v for k, v in VALID.items() if k != "true_verdict"} | {"true_verdit": "overreach"}

    with pytest.raises(ValueError, match="unknown fields"):
        parse_item(bad, "set.jsonl:3")


def test_missing_or_empty_required_field_names_the_line() -> None:
    with pytest.raises(ValueError, match=r"set\.jsonl:5: 'claim'"):
        parse_item({**VALID, "claim": "  "}, "set.jsonl:5")


def test_an_item_must_carry_at_least_one_quote() -> None:
    with pytest.raises(ValueError, match="'quotes'"):
        parse_item({**VALID, "quotes": []})

    with pytest.raises(ValueError, match="'quotes'"):
        parse_item({**VALID, "quotes": "Q."})


def test_several_quotes_are_kept_in_order() -> None:
    item = parse_item({**VALID, "quotes": ["First.", "Second."]})

    assert item.quotes == ("First.", "Second.")


def test_secondhand_must_be_a_boolean_or_null() -> None:
    with pytest.raises(ValueError, match="secondhand"):
        parse_item({**VALID, "secondhand": "yes"})


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate id"):
        load_items(_write(tmp_path, VALID, VALID))


# ── Running ──────────────────────────────────────────────────────────────────

async def test_run_predictor_returns_predictions_in_input_order() -> None:
    items = [_item(n, "supported") for n in range(1, 6)]
    predictor = _FakePredictor({f"i{n}": "supported" for n in range(1, 6)})

    predictions = await run_predictor(items, predictor, concurrency=5)

    assert [p.item_id for p in predictions] == ["i1", "i2", "i3", "i4", "i5"]


async def test_run_predictor_rejects_an_answer_for_the_wrong_item() -> None:
    class _Confused:
        name = "confused"

        async def predict(self, item: EvalItem) -> Prediction:
            return Prediction("someone-else", "supported")

    with pytest.raises(ValueError, match="someone-else"):
        await run_predictor([_item(1, "supported")], _Confused())


# ── Scoring ──────────────────────────────────────────────────────────────────

def test_score_refuses_to_pool_sets() -> None:
    items = [_item(1, "supported", "set-a"), _item(2, "supported", "set-b")]

    with pytest.raises(ValueError, match="one set at a time"):
        predictions = [Prediction("i1", "supported"), Prediction("i2", "supported")]
        score(items, predictions, SPEC_GRADED, "x")


def test_score_requires_a_prediction_for_every_item() -> None:
    items = [_item(1, "supported"), _item(2, "overreach")]

    with pytest.raises(ValueError, match="no prediction"):
        score(items, [Prediction("i1", "supported")], SPEC_GRADED, "x")


def test_binary_predictor_gets_dangerous_errors_but_no_macro_f1() -> None:
    items = [
        _item(1, "supported"), _item(2, "overreach"), _item(3, "overreach"), _item(4, "misread"),
    ]
    predictions = [
        Prediction("i1", "supported"),
        Prediction("i2", "supported"),
        Prediction("i3", "not_supported"),
        Prediction("i4", "not_supported"),
    ]

    report = score(items, predictions, SPEC_BINARY, "audit-binary")

    assert report.macro is None
    assert (report.dangerous.overall.count, report.dangerous.overall.n) == (1, 3)
    assert [s.label for s in report.scores] == ["supported"]


def test_graded_predictor_gets_macro_f1() -> None:
    items = [_item(1, "supported"), _item(2, "overreach"), _item(3, "misread")]
    predictions = [Prediction(i.id, i.true_verdict) for i in items]

    report = score(items, predictions, SPEC_GRADED, "audit-graded")

    assert report.macro is not None
    assert report.macro.value == pytest.approx(1.0)


# ── Report ───────────────────────────────────────────────────────────────────

def _report_text(spec: ScoringSpec) -> str:
    items = [_item(1, "supported"), _item(2, "overreach"), _item(3, "misread")]
    predictions = [Prediction(f"i{n}", "supported") for n in (1, 2, 3)]
    return render_markdown(score(items, predictions, spec, "audit-binary"), spec)


def test_report_sections_appear_in_evidence_order() -> None:
    text = _report_text(SPEC_BINARY)

    positions = [text.index(f"## {n}.") for n in range(1, 5)]
    assert positions == sorted(positions)
    assert "## 1. Confusion matrix" in text
    assert "## 3. Bad citations predicted `supported`" in text


def test_report_shows_undefined_values_as_na_and_no_macro_for_binary() -> None:
    text = _report_text(SPEC_BINARY)

    assert "n/a" in text
    assert "Not computed" in text


def test_report_carries_no_evaluative_language() -> None:
    """A lightweight guard on the plan's rule: numbers first, no verdict."""
    text = _report_text(SPEC_GRADED).lower()

    for word in ("good", "poor", "excellent", "acceptable", "strong", "weak", "recommend"):
        assert word not in text


async def test_one_failing_item_does_not_lose_the_rest_of_the_run() -> None:
    """A paid run must keep the results it already has; the failure is carried
    as its own verdict so the report can count it."""
    class _SometimesBroken:
        name = "sometimes-broken"

        async def predict(self, item: EvalItem) -> Prediction:
            if item.id == "i2":
                raise RuntimeError("malformed response")
            return Prediction(item.id, "supported")

    items = [_item(1, "supported"), _item(2, "supported"), _item(3, "supported")]

    predictions = await run_predictor(items, _SometimesBroken())

    assert [p.verdict for p in predictions] == ["supported", PREDICTION_ERROR, "supported"]
    assert "malformed response" in predictions[1].rationale
