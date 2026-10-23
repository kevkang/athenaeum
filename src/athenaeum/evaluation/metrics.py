"""Classification metrics for evaluating Audit against labeled citations.

Written out in plain Python rather than imported from a statistics library:
each is a few lines, these are the definitions a published number rests on,
and anyone checking the method should be able to read exactly what was
computed. Every function is generic over label names, so the same code
scores a binary predictor (supported / not) against graded true labels.

Undefined values are None, never 0. A precision with no predictions behind
it, or a rate over zero items, is "not measured", and reporting it as 0 would
be a claim the data doesn't support.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import NormalDist

# ── Intervals ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Interval:
    low: float
    high: float


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> Interval | None:
    """Wilson score interval for a proportion `successes / n`.

    Used instead of the textbook normal approximation, p +/- z*sqrt(p(1-p)/n),
    because that one fails exactly where citation error rates live: small
    proportions and modest samples. It can extend below 0, and it collapses to
    a zero-width interval when no errors are observed, as if 0/50 proved the
    true rate is exactly 0. Returns None when n is 0 (nothing was measured).
    """
    if n == 0:
        return None
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be between 0 and n, got {successes}/{n}")
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    p = successes / n
    z2 = z * z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return Interval(max(0.0, centre - half_width), min(1.0, centre + half_width))


@dataclass(frozen=True)
class Proportion:
    """A count out of a total, with its rate and Wilson interval together, so a
    rate can never be reported without its sample size."""

    count: int
    n: int

    @property
    def rate(self) -> float | None:
        return self.count / self.n if self.n else None

    @property
    def interval(self) -> Interval | None:
        return wilson_interval(self.count, self.n)


# ── Confusion matrix and per-label scores ────────────────────────────────────


@dataclass(frozen=True)
class ConfusionMatrix:
    """Counts of (true label, predicted label) pairs.

    True and predicted labels are listed separately because they can differ:
    a binary predictor answers only supported / not_supported, while the
    labeled data carries graded verdicts.
    """

    true_labels: tuple[str, ...]
    predicted_labels: tuple[str, ...]
    counts: Counter[tuple[str, str]]

    def count(self, true: str, predicted: str) -> int:
        return self.counts[(true, predicted)]

    def support(self, true: str) -> int:
        """How many items truly carry this label (the row total)."""
        return sum(self.count(true, p) for p in self.predicted_labels)

    def predicted_total(self, predicted: str) -> int:
        """How many items were predicted as this label (the column total)."""
        return sum(self.count(t, predicted) for t in self.true_labels)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def confusion_matrix(
    true: Sequence[str],
    predicted: Sequence[str],
    true_labels: Sequence[str] | None = None,
    predicted_labels: Sequence[str] | None = None,
) -> ConfusionMatrix:
    """Tally aligned true/predicted label sequences.

    Label lists fix row and column order for reporting; when omitted, they are
    the labels seen, sorted. A label outside a given list is an error rather
    than silently dropped, since dropping items would change every rate.
    """
    if len(true) != len(predicted):
        raise ValueError(f"{len(true)} true labels but {len(predicted)} predictions")
    rows = tuple(true_labels) if true_labels is not None else tuple(sorted(set(true)))
    cols = (
        tuple(predicted_labels)
        if predicted_labels is not None
        else tuple(sorted(set(predicted)))
    )
    unknown = (set(true) - set(rows)) | (set(predicted) - set(cols))
    if unknown:
        raise ValueError(f"labels not in the given label lists: {sorted(unknown)}")
    return ConfusionMatrix(rows, cols, Counter(zip(true, predicted, strict=True)))


@dataclass(frozen=True)
class LabelScores:
    label: str
    precision: float | None  # None when the label was never predicted
    recall: float | None  # None when no item truly has the label
    f1: float | None  # None when no item truly has the label
    support: int


def label_scores(cm: ConfusionMatrix, label: str) -> LabelScores:
    """Precision, recall and F1 for one label that appears in both the true
    and predicted label spaces.

    Precision: of the items predicted as this label, the share that truly are.
    Recall: of the items that truly are this label, the share predicted as it.
    F1 is their harmonic mean. A label that truly occurs but was never
    predicted has recall 0, so its F1 is 0 (a real miss), even though its
    precision is undefined.
    """
    hits = cm.count(label, label)
    support = cm.support(label)
    predicted = cm.predicted_total(label)
    precision = hits / predicted if predicted else None
    recall = hits / support if support else None
    if recall is None:
        return LabelScores(label, precision, None, None, support)
    p = precision or 0.0
    f1 = 0.0 if p + recall == 0 else 2 * p * recall / (p + recall)
    return LabelScores(label, precision, recall, f1, support)


@dataclass(frozen=True)
class MacroF1:
    value: float | None
    # Labels averaged over, and labels left out because nothing truly had them.
    included: tuple[str, ...]
    excluded: tuple[str, ...]


def macro_f1(cm: ConfusionMatrix, labels: Iterable[str]) -> MacroF1:
    """Unweighted mean F1 over `labels`, so a rare verdict counts as much as a
    common one. Labels with no true items are left out and named, rather than
    counted as 0 or 1, because the set simply didn't test them."""
    included: list[str] = []
    excluded: list[str] = []
    f1s: list[float] = []
    for label in labels:
        scores = label_scores(cm, label)
        if scores.f1 is None:
            excluded.append(label)
        else:
            included.append(label)
            f1s.append(scores.f1)
    value = sum(f1s) / len(f1s) if f1s else None
    return MacroF1(value, tuple(included), tuple(excluded))


# ── The error that matters most ──────────────────────────────────────────────


@dataclass(frozen=True)
class DangerousErrors:
    overall: Proportion
    by_true_label: dict[str, Proportion]


def dangerous_errors(
    cm: ConfusionMatrix, bad_labels: Iterable[str], safe_prediction: str
) -> DangerousErrors:
    """Among items whose true label is bad, the share predicted as safe, overall
    and broken out by true label.

    This is the failure a green check exists to prevent: a bad citation shown
    as supported. Confusing one bad label for another (overreach called
    misread) costs little, since the author investigates either way, so those
    errors don't count here.
    """
    by_label = {
        label: Proportion(cm.count(label, safe_prediction), cm.support(label))
        for label in bad_labels
    }
    overall = Proportion(
        sum(p.count for p in by_label.values()), sum(p.n for p in by_label.values())
    )
    return DangerousErrors(overall, by_label)


# ── Agreement between two labelers ───────────────────────────────────────────


def cohen_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> float | None:
    """Agreement between two labelers, corrected for agreement expected by chance.

    kappa = (observed - expected) / (1 - expected), where `expected` is the
    chance two labelers who each used labels at their own observed rates would
    agree. 1 is perfect agreement and 0 is no better than chance. Low kappa on
    a verdict taxonomy suggests the categories themselves are unclear. None
    when there are no items, or when chance agreement is already total (both
    labelers used one and the same label for everything).
    """
    if len(labels_a) != len(labels_b):
        raise ValueError(f"{len(labels_a)} labels from A but {len(labels_b)} from B")
    n = len(labels_a)
    if n == 0:
        return None
    observed = sum(a == b for a, b in zip(labels_a, labels_b, strict=True)) / n
    counts_a, counts_b = Counter(labels_a), Counter(labels_b)
    expected = sum(counts_a[label] * counts_b[label] for label in counts_a) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)
