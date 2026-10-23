"""Tests for the binary-versus-graded tabulation.

The rules worth protecting here are reporting rules, not arithmetic: a rate
under ~100 items must carry its interval, a subgroup too small for a rate must
not be given one, and a verdict recovered from a single quote must be counted
the same way whether the run predates the recovery rule or not.
"""
from __future__ import annotations

from pathlib import Path

from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.tabulate import render

SET = "set-a1"


def _item(n: int, verdict: str, quotes: int = 1) -> EvalItem:
    return EvalItem(
        id=f"i{n}",
        claim=f"Claim {n}.",
        quotes=tuple(f"Quote {n}.{q}" for q in range(quotes)),
        true_verdict=verdict,
        source=SET,
    )


def _binary(item_id: str, verdict: str, quote_verdicts: tuple[str, ...] = ()) -> dict:
    return {
        "item_id": item_id, "verdict": verdict, "rationale": "r",
        "quote_verdicts": list(quote_verdicts),
    }


def _graded(item_id: str, verdict: str, quote_verdicts: tuple[str, ...] = ()) -> dict:
    return {
        "item_id": item_id, "verdict": verdict, "rationale": "r",
        "quote_verdicts": list(quote_verdicts),
    }


def test_a_small_rate_carries_its_interval_and_denominator() -> None:
    items = [_item(n, "supported") for n in range(1, 6)]
    baseline = {i.id: _binary(i.id, "not_supported") for i in items}
    graded = {i.id: _graded(i.id, "overreach") for i in items}

    text = render(items, baseline, graded)

    assert "0 of 5 (0.0%)" in text
    assert "95% CI" in text


def test_the_multi_sentence_subgroup_is_reported_without_a_rate() -> None:
    """Seven items produce rates like 87.5% whose interval covers most of the
    range; printing one invites a comparison the sample can't support."""
    items = [_item(1, "supported", quotes=2), _item(2, "supported", quotes=2)]
    baseline = {
        i.id: _binary(i.id, "not_supported", ("supported", "not_supported")) for i in items
    }
    graded = {"i1": _graded("i1", "supported"), "i2": _graded("i2", "misread")}

    text = render(items, baseline, graded)

    assert "Multi-sentence disagreements the graded variant calls `supported`: 1 of 2" in text
    assert "No rate is given" in text


def test_only_items_the_two_runs_disagree_on_are_tabulated() -> None:
    items = [_item(1, "supported"), _item(2, "supported"), _item(3, "misread")]
    baseline = {
        "i1": _binary("i1", "not_supported"),
        "i2": _binary("i2", "supported"),
        "i3": _binary("i3", "not_supported"),
    }
    graded = {"i1": _graded("i1", "overreach")}

    text = render(items, baseline, graded)

    # i2 agrees with the label and i3 is not a `supported` label, so one item.
    assert "1 items the set labels `supported` and the binary run called not supported" in text


def test_a_recorded_error_with_one_quote_verdict_is_read_as_that_verdict() -> None:
    """The run was recorded before the recovery rule existed. Reading it with
    the rule must give what a run recorded after the rule would have given."""
    items = [_item(1, "supported")]
    baseline = {"i1": _binary("i1", "not_supported")}
    graded = {"i1": _graded("i1", "error", ("overreach",))}

    text = render(items, baseline, graded)

    assert "| single-sentence rationale (n=1) | 0 | 0 | 1 | 0 | 0 | 1 |" in text
    assert "1 single-quote items returned no set verdict" in text


def test_a_multi_quote_error_is_not_recovered_into_a_verdict() -> None:
    items = [_item(1, "supported", quotes=2)]
    baseline = {"i1": _binary("i1", "not_supported", ("not_supported", "not_supported"))}
    graded = {"i1": _graded("i1", "error", ("supported", "supported"))}

    text = render(items, baseline, graded)

    assert "| multi-sentence rationale (n=1) | 0 | 0 | 0 | 0 | 1 | 1 |" in text


def test_zero_indeterminate_is_reported_as_unavailable_not_as_a_finding() -> None:
    """The binary predictor cannot emit `indeterminate`, so its zero says
    nothing about the citations."""
    items = [_item(1, "supported")]
    baseline = {"i1": _binary("i1", "supported")}

    text = render(items, baseline, {})

    assert "zero by construction rather than by observation" in text


# ── Repeat agreement ─────────────────────────────────────────────────────────

def test_repeat_agreement_separates_the_category_from_the_decision() -> None:
    """A run where only fault categories move is a different thing from one
    where items cross the supported line, and the two must not be one number."""
    from athenaeum.evaluation.stability import render

    a = {"i1": "misread", "i2": "overreach", "i3": "supported"}
    b = {"i1": "overreach", "i2": "overreach", "i3": "supported"}

    text = render(a, b)

    assert "Same named verdict: 2 of 3" in text
    assert "Same decision, supported against everything else: 3 of 3" in text
    assert "| `i1` | `misread` | `overreach` | no |" in text


def test_repeat_agreement_flags_an_item_that_crosses_the_supported_line() -> None:
    from athenaeum.evaluation.stability import render

    text = render({"i1": "supported"}, {"i1": "misread"})

    assert "| `i1` | `supported` | `misread` | yes |" in text


def test_repeat_agreement_reads_a_recovered_verdict_from_an_older_run(tmp_path: Path) -> None:
    """A run recorded before `set_verdict_recovered` existed must compare the
    same way as one recorded after."""
    import json

    from athenaeum.evaluation.stability import load_verdicts

    (tmp_path / "predictions.jsonl").write_text(
        json.dumps({"item_id": "i1", "verdict": "error", "quote_verdicts": ["overreach"]}) + "\n",
        encoding="utf-8",
    )

    assert load_verdicts(tmp_path) == {"i1": "overreach"}
