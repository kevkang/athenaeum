"""Run a predictor over one labeled set and score it.

The report follows the plan's order of evidence: confusion matrix first, then
per-verdict scores, then the dangerous error rate by true verdict with
intervals, then macro-F1. It states what was measured and nothing more. There
is deliberately no summary verdict or headline, so readers form conclusions
from the numbers rather than from our framing of them.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.metrics import (
    ConfusionMatrix,
    DangerousErrors,
    Interval,
    LabelScores,
    MacroF1,
    confusion_matrix,
    dangerous_errors,
    label_scores,
    macro_f1,
)

# Recorded when a predictor raises for one item: the run keeps its other
# results, and the failure stays visible in the report instead of vanishing.
PREDICTION_ERROR: Final = "error"


@dataclass(frozen=True)
class Prediction:
    item_id: str
    # The item-level verdict, after rolling up per-quote verdicts.
    verdict: str
    rationale: str = ""
    # One verdict per quote on the item, kept so a multi-quote item's rollup
    # can be inspected rather than taken on trust.
    quote_verdicts: tuple[str, ...] = ()


class Predictor(Protocol):
    """Anything that gives a verdict for one labeled citation. `name` is
    recorded in every report, so results always say what produced them."""

    name: str

    async def predict(self, item: EvalItem) -> Prediction: ...


async def run_predictor(
    items: Sequence[EvalItem], predictor: Predictor, concurrency: int = 4
) -> list[Prediction]:
    """Predict every item, at most `concurrency` at a time, and return the
    predictions in the same order as `items`."""
    semaphore = asyncio.Semaphore(concurrency)

    async def predict_one(item: EvalItem) -> Prediction:
        async with semaphore:
            try:
                prediction = await predictor.predict(item)
            except Exception as exc:
                # One malformed response must not discard every other result in
                # a paid run; the error is carried as its own verdict.
                return Prediction(item.id, PREDICTION_ERROR, f"{type(exc).__name__}: {exc}")
        if prediction.item_id != item.id:
            raise ValueError(f"predictor returned {prediction.item_id!r} for item {item.id!r}")
        return prediction

    return list(await asyncio.gather(*(predict_one(item) for item in items)))


@dataclass(frozen=True)
class ScoringSpec:
    """What the labels mean for one comparison between a set and a predictor."""

    # Verdicts the set's items can carry, in report order.
    true_labels: tuple[str, ...]
    # Verdicts the predictor can emit, in report order.
    predicted_labels: tuple[str, ...]
    # Verdicts averaged in macro-F1. Verdicts decided by a deterministic check
    # rather than the model are left out, so a category that is right by
    # construction can't inflate the average.
    judgment_labels: tuple[str, ...]
    # True verdicts meaning the citation is wrong in a way a reader must be warned about.
    bad_labels: tuple[str, ...]
    # The prediction an author sees as a pass.
    safe_prediction: str


@dataclass(frozen=True)
class Report:
    source: str
    predictor: str
    n: int
    matrix: ConfusionMatrix
    # Scores for verdicts present in both the true and predicted label lists.
    scores: tuple[LabelScores, ...]
    dangerous: DangerousErrors
    # None when the predictor can't emit every judgment verdict (today's
    # pass/fail Audit, for example): an average over verdicts it can never
    # produce would be meaningless.
    macro: MacroF1 | None


def score(
    items: Sequence[EvalItem],
    predictions: Sequence[Prediction],
    spec: ScoringSpec,
    predictor_name: str,
) -> Report:
    """Score predictions against one labeled set.

    Refuses items from more than one set: sets built differently measure
    different things, and a pooled number would describe neither. Every item
    must have a prediction; a missing one would silently shrink the sample.
    """
    sources = {item.source for item in items}
    if len(sources) != 1:
        raise ValueError(f"score one set at a time; got sources {sorted(sources)}")
    by_id = {p.item_id: p for p in predictions}
    missing = [item.id for item in items if item.id not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} items have no prediction, e.g. {missing[:3]}")

    true = [item.true_verdict for item in items]
    predicted = [by_id[item.id].verdict for item in items]
    matrix = confusion_matrix(true, predicted, spec.true_labels, spec.predicted_labels)
    shared = [label for label in spec.true_labels if label in spec.predicted_labels]
    macro = (
        macro_f1(matrix, spec.judgment_labels)
        if set(spec.judgment_labels) <= set(spec.predicted_labels)
        else None
    )
    return Report(
        source=next(iter(sources)),
        predictor=predictor_name,
        n=len(items),
        matrix=matrix,
        scores=tuple(label_scores(matrix, label) for label in shared),
        dangerous=dangerous_errors(matrix, spec.bad_labels, spec.safe_prediction),
        macro=macro,
    )


# ── Rendering ────────────────────────────────────────────────────────────────


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _interval(interval: Interval | None) -> str:
    if interval is None:
        return "n/a"
    return f"{interval.low * 100:.1f}% to {interval.high * 100:.1f}%"


def _code_list(labels: Sequence[str]) -> str:
    return ", ".join(f"`{label}`" for label in labels)


def render_markdown(report: Report, spec: ScoringSpec, notes: Sequence[str] = ()) -> str:
    """The report as Markdown: four numbered sections, each a table with a
    caption that says what the numbers are, in the plan's order.

    `notes` are statements of fact about the run itself — how many of the
    model's answers could not be used, for instance — printed under the item
    count so a reader sees them before any score.
    """
    m = report.matrix
    safe = spec.safe_prediction
    lines = [
        f"# Audit evaluation: `{report.predictor}` on `{report.source}`",
        "",
        f"{report.n} labeled citations.",
        "",
        *([*notes, ""] if notes else []),
        "## 1. Confusion matrix",
        "",
        "| true verdict | "
        + " | ".join(f"predicted `{p}`" for p in m.predicted_labels)
        + " | total |",
        "|---" * (len(m.predicted_labels) + 2) + "|",
    ]
    for t in m.true_labels:
        cells = " | ".join(str(m.count(t, p)) for p in m.predicted_labels)
        lines.append(f"| `{t}` | {cells} | {m.support(t)} |")
    lines += [
        "",
        "*Each cell counts citations whose true verdict is the row and whose predicted "
        "verdict is the column.*",
        "",
        "## 2. Per-verdict scores",
        "",
        "| verdict | precision | recall | F1 | support |",
        "|---|---|---|---|---|",
    ]
    for s in report.scores:
        lines.append(
            f"| `{s.label}` | {_number(s.precision)} | {_number(s.recall)} | "
            f"{_number(s.f1)} | {s.support} |"
        )
    lines += [
        "",
        "*Precision: of citations predicted as the verdict, the share that truly carry it. "
        "Recall: of citations that truly carry it, the share predicted as it. n/a: "
        "undefined, because the verdict was never predicted or never occurs in the set.*",
        "",
        f"## 3. Bad citations predicted `{safe}`",
        "",
        f"| true verdict | predicted `{safe}` | citations | rate | 95% interval |",
        "|---|---|---|---|---|",
    ]
    d = report.dangerous
    for label, p in d.by_true_label.items():
        lines.append(
            f"| `{label}` | {p.count} | {p.n} | {_percent(p.rate)} | {_interval(p.interval)} |"
        )
    lines += [
        f"| all bad verdicts | {d.overall.count} | {d.overall.n} | "
        f"{_percent(d.overall.rate)} | {_interval(d.overall.interval)} |",
        "",
        f"*Rate: share of citations with the row's true verdict that received the `{safe}` "
        "prediction. Interval: Wilson score interval.*",
        "",
        "## 4. Macro-F1",
        "",
    ]
    if report.macro is None:
        lines.append(
            f"Not computed: `{report.predictor}` does not emit every judgment verdict "
            f"({_code_list(spec.judgment_labels)})."
        )
    else:
        lines.append(
            f"Macro-F1 over {_code_list(report.macro.included)}: {_number(report.macro.value)}."
        )
        if report.macro.excluded:
            lines.append(
                "Left out because no citation in the set carries them: "
                f"{_code_list(report.macro.excluded)}."
            )
    return "\n".join(lines) + "\n"
