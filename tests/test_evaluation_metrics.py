"""Tests for the evaluation metrics, checked against hand-computed values.

These numbers end up published with confidence intervals, so each test pins a
definition to a value worked out independently: a textbook kappa example, a
Wilson interval computed by hand, and small confusion matrices whose scores
can be read off by eye.
"""
from __future__ import annotations

import pytest

from athenaeum.evaluation.metrics import (
    Proportion,
    cohen_kappa,
    confusion_matrix,
    dangerous_errors,
    label_scores,
    macro_f1,
    wilson_interval,
)

# ── Wilson interval ──────────────────────────────────────────────────────────

def test_wilson_interval_matches_hand_computation() -> None:
    """10 errors in 200: about 5% with roughly +/-3 points, the example in the plan."""
    interval = wilson_interval(10, 200)

    assert interval is not None
    assert interval.low == pytest.approx(0.0274, abs=5e-4)
    assert interval.high == pytest.approx(0.0896, abs=5e-4)


def test_zero_observed_errors_still_has_a_nonzero_upper_bound() -> None:
    """0 of 50 is not proof the true rate is 0; the naive interval would say it is."""
    interval = wilson_interval(0, 50)

    assert interval is not None
    assert interval.low == 0.0
    assert interval.high == pytest.approx(0.0713, abs=5e-4)


def test_nothing_measured_has_no_interval_or_rate() -> None:
    assert wilson_interval(0, 0) is None
    assert Proportion(0, 0).rate is None
    assert Proportion(0, 0).interval is None


def test_impossible_counts_are_rejected() -> None:
    with pytest.raises(ValueError):
        wilson_interval(6, 5)


# ── Confusion matrix and scores ──────────────────────────────────────────────

TRUE = ["supported", "supported", "overreach", "overreach", "misread", "misread"]
PRED = ["supported", "overreach", "supported", "overreach", "overreach", "misread"]


def test_confusion_matrix_counts_pairs() -> None:
    cm = confusion_matrix(TRUE, PRED)

    assert cm.count("overreach", "supported") == 1
    assert cm.count("misread", "overreach") == 1
    assert cm.support("overreach") == 2
    assert cm.predicted_total("overreach") == 3
    assert cm.total == 6


def test_label_scores_read_off_the_matrix() -> None:
    cm = confusion_matrix(TRUE, PRED)

    scores = label_scores(cm, "overreach")

    # Predicted overreach 3 times, right once; truly overreach twice, caught once.
    assert scores.precision == pytest.approx(1 / 3)
    assert scores.recall == pytest.approx(1 / 2)
    assert scores.f1 == pytest.approx(0.4)


def test_label_never_predicted_has_undefined_precision_but_zero_f1() -> None:
    cm = confusion_matrix(["misread", "misread"], ["supported", "supported"],
                          true_labels=["misread"], predicted_labels=["supported", "misread"])

    scores = label_scores(cm, "misread")

    assert scores.precision is None
    assert scores.recall == 0.0
    assert scores.f1 == 0.0


def test_macro_f1_excludes_and_names_labels_the_set_never_tested() -> None:
    labels = ["supported", "overreach", "misread", "indeterminate"]
    cm = confusion_matrix(TRUE, PRED, true_labels=labels, predicted_labels=labels)

    result = macro_f1(cm, labels)

    assert result.excluded == ("indeterminate",)
    assert result.included == ("supported", "overreach", "misread")
    expected = (label_scores(cm, "supported").f1 + 0.4 + label_scores(cm, "misread").f1) / 3
    assert result.value == pytest.approx(expected)


def test_labels_outside_the_given_lists_are_an_error_not_dropped() -> None:
    with pytest.raises(ValueError, match="fabricated"):
        confusion_matrix(["fabricated"], ["supported"],
                         true_labels=["supported"], predicted_labels=["supported"])


def test_a_binary_predictor_scores_against_graded_truth() -> None:
    """Today's Audit answers only supported / not_supported; the harness must
    still be able to score it against graded labels."""
    true = ["supported", "overreach", "overreach", "misread"]
    pred = ["supported", "supported", "not_supported", "not_supported"]

    cm = confusion_matrix(true, pred)

    assert cm.predicted_labels == ("not_supported", "supported")
    assert cm.count("overreach", "supported") == 1


# ── Dangerous errors ─────────────────────────────────────────────────────────

def test_dangerous_errors_count_only_bad_citations_shown_as_supported() -> None:
    cm = confusion_matrix(TRUE, PRED)

    result = dangerous_errors(cm, bad_labels=["overreach", "misread"], safe_prediction="supported")

    # One overreach was called supported; the misread called overreach doesn't count.
    assert (result.overall.count, result.overall.n) == (1, 4)
    assert (result.by_true_label["overreach"].count, result.by_true_label["overreach"].n) == (1, 2)
    assert result.by_true_label["misread"].count == 0
    assert result.overall.interval is not None


# ── Cohen's kappa ────────────────────────────────────────────────────────────

def test_kappa_matches_the_textbook_example() -> None:
    """50 items: both yes 20, A yes/B no 5, A no/B yes 10, both no 15.
    Observed agreement 0.7, chance agreement 0.5, kappa 0.4."""
    a = ["yes"] * 20 + ["yes"] * 5 + ["no"] * 10 + ["no"] * 15
    b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15

    assert cohen_kappa(a, b) == pytest.approx(0.4)


def test_kappa_is_one_for_perfect_agreement_and_undefined_when_chance_is_total() -> None:
    assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)
    assert cohen_kappa(["a", "a"], ["a", "a"]) is None
    assert cohen_kappa([], []) is None
