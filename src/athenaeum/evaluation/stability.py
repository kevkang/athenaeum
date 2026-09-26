"""How much of a graded run repeats when the same items are run again.

    python -m athenaeum.evaluation.stability --a <run dir> --b <run dir>

Costs nothing: it compares two runs that already happened. Two runs of the
same prompt on the same items should mostly agree; how much they don't is a
floor under every difference measured between any two runs. A category count
that moves by less than this says nothing.

Reported at two grains, because they can differ sharply: the named verdict,
and the same verdicts collapsed to supported against everything else — which
is the decision a reader of the Audit panel actually acts on.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from athenaeum.domain.audit_verdicts import SUPPORTED
from athenaeum.evaluation.graded import verdict_from_row
from athenaeum.evaluation.metrics import Proportion


def load_verdicts(directory: Path) -> dict[str, str]:
    """Every recorded verdict in a run, with the recovery rule applied."""
    verdicts: dict[str, str] = {}
    for line in (directory / "predictions.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        verdicts[row["item_id"]] = verdict_from_row(row)[0]
    return verdicts


def _rate(label: str, p: Proportion) -> str:
    if p.rate is None:
        return f"{label}: no shared items."
    body = f"{label}: {p.count} of {p.n} ({p.rate:.1%})"
    if p.interval is not None:
        body += f", 95% CI {p.interval.low:.1%} to {p.interval.high:.1%}"
    return body + "."


def render(a: dict[str, str], b: dict[str, str], names: Sequence[str] = ("A", "B")) -> str:
    shared = sorted(set(a) & set(b))
    identical = [i for i in shared if a[i] == b[i]]
    same_decision = [i for i in shared if (a[i] == SUPPORTED) == (b[i] == SUPPORTED)]
    lines = [
        f"# Repeat agreement: `{names[0]}` against `{names[1]}`",
        "",
        f"{len(shared)} items appear in both runs "
        f"({len(a)} and {len(b)} items respectively).",
        "",
        "- " + _rate("Same named verdict", Proportion(len(identical), len(shared))),
        "- " + _rate(
            "Same decision, supported against everything else",
            Proportion(len(same_decision), len(shared)),
        ),
        "",
        "Every difference, in full:",
        "",
        f"| item | {names[0]} | {names[1]} | crosses supported? |",
        "|---|---|---|---|",
    ]
    for item_id in shared:
        if a[item_id] == b[item_id]:
            continue
        crosses = "yes" if (a[item_id] == SUPPORTED) != (b[item_id] == SUPPORTED) else "no"
        lines.append(f"| `{item_id}` | `{a[item_id]}` | `{b[item_id]}` | {crosses} |")
    if len(identical) == len(shared):
        lines.append("| — | — | — | — |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--a", required=True, type=Path)
    parser.add_argument("--b", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    text = render(load_verdicts(args.a), load_verdicts(args.b), (args.a.name, args.b.name))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
