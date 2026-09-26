"""Cross-tabulate a binary Audit run against a graded re-run of chosen items.

    python -m athenaeum.evaluation.tabulate \\
        --items eval/audit/sets/set-a1-scifact.jsonl \\
        --baseline <dir with the full-set binary run> \\
        --graded <dir with the graded re-run>

Reads two runs that already happened and costs nothing. It answers one
question the binary run could not: when Audit said a citation was not
supported and the label said it was, *what kind* of disagreement was it?

Reporting rules, from the plan and from what the earlier reporting got wrong:

- Every count carries its denominator.
- A rate from fewer than ~100 items carries a Wilson interval.
- Subgroups too small to carry a rate are reported as counts only. Eight
  items produce rates like 87.5% whose interval spans most of the range;
  printing one invites a comparison the sample cannot support.
- Nothing here is a summary verdict. The tables state what was observed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from athenaeum.domain.audit_verdicts import JUDGMENT_VERDICTS, SUPPORTED
from athenaeum.evaluation.graded import verdict_from_row as _graded_verdict
from athenaeum.evaluation.items import EvalItem, load_items
from athenaeum.evaluation.metrics import Proportion
from athenaeum.evaluation.runner import PREDICTION_ERROR

# Verdict columns for every table, in taxonomy order with the run outcome last.
_COLUMNS = (*JUDGMENT_VERDICTS, PREDICTION_ERROR)


def _load_predictions(directory: Path) -> dict[str, dict]:
    rows = [
        json.loads(line)
        for line in (directory / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {row["item_id"]: row for row in rows}


def _rate_line(label: str, p: Proportion) -> str:
    """A rate with its denominator and, under ~100 items, its interval."""
    if p.rate is None:
        return f"{label}: no items, so no rate."
    body = f"{label}: {p.count} of {p.n} ({p.rate:.1%})"
    if p.n < 100 and p.interval is not None:
        body += f", 95% CI {p.interval.low:.1%} to {p.interval.high:.1%}"
    return body + "."


def _counts_table(title: str, rows: Sequence[tuple[str, Counter[str]]]) -> list[str]:
    lines = [
        title,
        "",
        "| group | " + " | ".join(f"`{c}`" for c in _COLUMNS) + " | total |",
        "|---" * (len(_COLUMNS) + 2) + "|",
    ]
    for name, counts in rows:
        cells = " | ".join(str(counts[c]) for c in _COLUMNS)
        lines.append(f"| {name} | {cells} | {sum(counts.values())} |")
    return [*lines, ""]


def render(
    items: Sequence[EvalItem], baseline: dict[str, dict], graded: dict[str, dict]
) -> str:
    """The tabulation as Markdown: counts first, rates only where the sample
    can carry one, and no interpretation."""
    by_id = {item.id: item for item in items}
    disagreements = [
        item
        for item in items
        if item.true_verdict == SUPPORTED and baseline[item.id]["verdict"] != SUPPORTED
    ]
    single = [i for i in disagreements if len(i.quotes) == 1]
    multi = [i for i in disagreements if len(i.quotes) > 1]

    lines = [
        "# Set A1: what kind of disagreement?",
        "",
        f"{len(items)} labeled citations in the set. "
        f"{len(graded)} of them were re-run with the graded variant.",
        "",
        "The binary run and the graded re-run are different predictors asked "
        "different questions. The graded verdicts below describe the graded "
        "variant only; they are not a re-scoring of the binary run.",
        "",
    ]

    recovered = [i.id for i in items if i.id in graded and _graded_verdict(graded[i.id])[1]]
    if recovered:
        lines += [
            f"{len(recovered)} single-quote items returned no set verdict and take it from "
            "their only quote's verdict (the rule in evaluation/graded.py). With one quote "
            "the set is the quote. Items: " + ", ".join(f"`{i}`" for i in recovered) + ".",
            "",
        ]

    # ── 1. The disagreements by kind ────────────────────────────────────────
    graded_rows: list[tuple[str, Counter[str]]] = []
    for name, group in (("single-sentence rationale", single), ("multi-sentence rationale", multi)):
        counts: Counter[str] = Counter()
        for item in group:
            if item.id in graded:
                counts[_graded_verdict(graded[item.id])[0]] += 1
        graded_rows.append((f"{name} (n={len(group)})", counts))

    lines += [
        "## 1. The disagreements, by graded verdict",
        "",
        f"{len(disagreements)} items the set labels `supported` and the binary run called "
        f"not supported: {len(single)} with a single-sentence rationale, {len(multi)} with a "
        "multi-sentence one.",
        "",
    ]
    lines += _counts_table(
        "Graded verdict given to each of those items on the re-run:", graded_rows
    )
    lines += [
        "*Counts, not rates: this is the disagreeing subset, chosen because the two runs "
        "disagreed on it, so no proportion of it describes the set.*",
        "",
        "## 2. Disagreements the graded variant calls `supported`",
        "",
        "How many of those items the graded variant put in the label's own category. The "
        "rest it assigned to one of the fault categories above.",
        "",
    ]
    single_supported = sum(
        1 for i in single if i.id in graded and _graded_verdict(graded[i.id])[0] == SUPPORTED
    )
    multi_supported = sum(
        1 for i in multi if i.id in graded and _graded_verdict(graded[i.id])[0] == SUPPORTED
    )
    lines += [
        "- " + _rate_line(
            "Single-sentence disagreements the graded variant calls `supported`",
            Proportion(single_supported, len(single)),
        ),
        f"- Multi-sentence disagreements the graded variant calls `supported`: "
        f"{multi_supported} of {len(multi)}. No rate is given: {len(multi)} items cannot "
        "carry one.",
        "",
    ]

    # ── 3. indeterminate across the whole set ───────────────────────────────
    true_indeterminate = sum(1 for i in items if i.true_verdict == "indeterminate")
    binary_indeterminate = sum(
        1 for i in items if baseline[i.id]["verdict"] == "indeterminate"
    )
    graded_indeterminate = sum(
        1 for i in items if i.id in graded and _graded_verdict(graded[i.id])[0] == "indeterminate"
    )
    lines += [
        "## 3. `indeterminate` across the full set",
        "",
        f"- Items labeled `indeterminate` in the set: {true_indeterminate} of {len(items)}.",
        f"- Items the binary run called `indeterminate`: {binary_indeterminate}. The binary "
        "predictor emits only `supported`, `not_supported` and `error`, so this is zero by "
        "construction rather than by observation, and nothing was excluded from binary "
        "scoring on account of it.",
        f"- Items the graded re-run called `indeterminate`: {graded_indeterminate} of "
        f"{len(graded)} re-run.",
        "",
    ]

    # ── 4. Multi-quote items: set verdict against the two rollups ───────────
    multi_items = [i for i in items if len(i.quotes) > 1 and i.id in graded]
    lines += [
        "## 4. Multi-quote items: one set verdict against the old rollups",
        "",
        f"{len(multi_items)} items carry more than one quote. The binary run answered per "
        "quote and its item verdict came from a rollup rule applied afterwards. The graded "
        "re-run asked once about the quotes together.",
        "",
        "| item | quotes | true verdict | strict (all quotes) | any (one quote) | set verdict |",
        "|---|---|---|---|---|---|",
    ]
    for item in multi_items:
        quote_verdicts = baseline[item.id].get("quote_verdicts") or []
        strict = SUPPORTED if quote_verdicts and all(
            v == SUPPORTED for v in quote_verdicts
        ) else "not_supported"
        any_rule = SUPPORTED if any(v == SUPPORTED for v in quote_verdicts) else "not_supported"
        verdict, _ = _graded_verdict(graded[item.id])
        lines.append(
            f"| `{item.id}` | {len(item.quotes)} | `{item.true_verdict}` | `{strict}` | "
            f"`{any_rule}` | `{verdict}` |"
        )
    lines += [
        "",
        "*strict and any are the two rollups computed from the binary run's per-quote "
        "verdicts. The set verdict is what the graded variant answered when asked about "
        "the quotes as one body of evidence. The two columns come from different prompts, "
        "so a row where they differ does not isolate the rollup rule: the question changed "
        "as well. Counts only; no rate is given for a group of "
        f"{len(multi_items)}.*",
        "",
    ]
    agree_strict = sum(
        1
        for i in multi_items
        if (_graded_verdict(graded[i.id])[0] == SUPPORTED)
        == (
            bool(baseline[i.id].get("quote_verdicts"))
            and all(v == SUPPORTED for v in baseline[i.id]["quote_verdicts"])
        )
    )
    agree_any = sum(
        1
        for i in multi_items
        if (_graded_verdict(graded[i.id])[0] == SUPPORTED)
        == any(v == SUPPORTED for v in (baseline[i.id].get("quote_verdicts") or []))
    )
    lines += [
        f"- The set verdict's supported/not-supported split matches the strict rollup on "
        f"{agree_strict} of {len(multi_items)} items, and the any rollup on {agree_any}.",
        "",
    ]

    # ── 5. What each run failed to answer ───────────────────────────────────
    unjudged_marker = "No verdict returned for this item."
    binary_errors = sum(1 for i in items if baseline[i.id]["verdict"] == PREDICTION_ERROR)
    binary_unjudged = sum(
        1 for i in items if unjudged_marker in (baseline[i.id]["rationale"] or "")
    )
    graded_errors = sum(
        1 for r in graded.values() if _graded_verdict(r)[0] == PREDICTION_ERROR
    )
    lines += [
        "## 5. Response integrity",
        "",
        f"- Binary run: {binary_errors} of {len(items)} items produced no verdict; "
        f"{binary_unjudged} items carried at least one quote the model left unjudged.",
        f"- Graded re-run: {graded_errors} of {len(graded)} items produced no usable "
        "verdict after recovery.",
        "",
    ]
    unused = [i for i in by_id if i in graded and i not in {x.id for x in items}]
    if unused:
        lines.append(f"{len(unused)} graded predictions refer to items not in the set.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path, help="binary run directory")
    parser.add_argument("--graded", required=True, type=Path, help="graded re-run directory")
    parser.add_argument("--out", type=Path, default=None, help="write here instead of stdout")
    args = parser.parse_args()

    items = load_items(args.items)
    baseline = _load_predictions(args.baseline)
    graded = _load_predictions(args.graded)
    missing = [i.id for i in items if i.id not in baseline]
    if missing:
        print(f"{len(missing)} items have no baseline prediction, e.g. {missing[:3]}",
              file=sys.stderr)
        raise SystemExit(2)

    text = render(items, baseline, graded)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
