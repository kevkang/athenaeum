"""Command line: run a predictor over one labeled set and write its report.

    python -m athenaeum.evaluation.cli \\
        --items eval/audit/sets/set-a-scifact.jsonl \\
        --out eval/audit/results/2026-09-baseline

Every labeled citation costs one model call, so a run costs money and needs
ANTHROPIC_API_KEY (the api container has it). The output directory gets
predictions.jsonl, which keeps every verdict and rationale for review, and
report.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from athenaeum.config import get_settings
from athenaeum.evaluation.graded import GradedAuditPredictor, graded_spec
from athenaeum.evaluation.items import load_items
from athenaeum.evaluation.predictors import BinaryAuditPredictor, binary_spec
from athenaeum.evaluation.runner import (
    PREDICTION_ERROR,
    render_markdown,
    run_predictor,
    score,
)
from athenaeum.provider.anthropic_client import AnthropicProvider
from athenaeum.synthesize.audit import malformed_rate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--items", required=True, type=Path, help="labeled set (JSONL)")
    parser.add_argument("--out", required=True, type=Path, help="directory for results")
    parser.add_argument(
        "--predictor", default="audit-binary", choices=["audit-binary", "audit-graded"],
        help=(
            "what to evaluate: audit-binary is today's production pass/fail check; "
            "audit-graded is the evaluation-only variant that names a verdict for an "
            "item's quotes taken together (evaluation/graded.py)"
        ),
    )
    parser.add_argument("--concurrency", type=int, default=4, help="model calls at once")
    parser.add_argument(
        "--per-verdict", type=int, default=None,
        help="pilot runs: score only the first N items of each true verdict",
    )
    parser.add_argument(
        "--only-ids", type=Path, default=None,
        help=(
            "re-run just the item ids listed in this file, one per line. A chosen "
            "subset is not a sample of the set, so no report.md is written for it"
        ),
    )
    parser.add_argument(
        "--max-malformed-rate", type=float, default=None,
        help=(
            "fail the run when more than this share of evidence checks come back "
            "unusable (default: the audit_malformed_threshold setting)"
        ),
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set; every item needs a model call.", file=sys.stderr)
        return 2

    items = load_items(args.items)
    if args.per_verdict is not None:
        # Keep file order within each verdict so a pilot is reproducible.
        taken: Counter[str] = Counter()
        pilot = []
        for item in items:
            if taken[item.true_verdict] < args.per_verdict:
                taken[item.true_verdict] += 1
                pilot.append(item)
        items = pilot
    if args.only_ids is not None:
        wanted = [
            line.strip()
            for line in args.only_ids.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        by_id = {item.id: item for item in items}
        unknown = [i for i in wanted if i not in by_id]
        if unknown:
            print(f"--only-ids names {len(unknown)} unknown items, e.g. {unknown[:3]}",
                  file=sys.stderr)
            return 2
        items = [by_id[i] for i in wanted]
    model = settings.checker_model
    threshold = (
        args.max_malformed_rate
        if args.max_malformed_rate is not None
        else settings.audit_malformed_threshold
    )
    provider = AnthropicProvider(settings.anthropic_api_key, model)
    predictor: BinaryAuditPredictor | GradedAuditPredictor
    if args.predictor == "audit-graded":
        predictor, spec = GradedAuditPredictor(provider, model), graded_spec()
    else:
        predictor, spec = BinaryAuditPredictor(provider, model), binary_spec()

    predictions = await run_predictor(items, predictor, args.concurrency)

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for p in predictions:
            row: dict[str, object] = {
                "item_id": p.item_id,
                "verdict": p.verdict,
                "quote_verdicts": list(p.quote_verdicts),
                "rationale": p.rationale,
            }
            if isinstance(predictor, GradedAuditPredictor):
                row["secondhand"] = predictor.secondhand.get(p.item_id)
                row["confidence"] = predictor.confidence.get(p.item_id)
                row["set_verdict_recovered"] = predictor.recovered.get(p.item_id)
            handle.write(json.dumps(row) + "\n")
    # Response integrity first: it says how much of this run the model actually
    # answered, which bounds what any score below can mean.
    rate = malformed_rate(predictor.malformed_entries, predictor.quotes_checked)
    errors = sum(p.verdict == PREDICTION_ERROR for p in predictions)
    # Two denominators, because the failures have two shapes: an unusable
    # entry is about one quote, while an item that produced no verdict at all
    # is about the item. Dividing item failures by quotes would understate
    # them on every multi-quote item.
    error_rate = malformed_rate(errors, len(items))
    note = (
        f"Response integrity: {predictor.malformed_entries} of {predictor.quotes_checked} "
        f"evidence checks returned an entry that could not be used"
        + (f" ({rate:.1%})" if rate is not None else "")
        + f"; {predictor.missing_verdicts} quotes received no verdict. "
        f"{errors} of {len(items)} items produced no verdict at all"
        + (f" ({error_rate:.1%})" if error_rate is not None else "")
        + "."
    )
    if isinstance(predictor, GradedAuditPredictor) and predictor.missing_set_verdicts:
        note += (
            f" {predictor.missing_set_verdicts} of {len(items)} items returned no set "
            f"verdict; {predictor.recovered_set_verdicts} of those were single-quote items "
            "whose verdict was read off their only quote (evaluation/graded.py). Recovery "
            "loses no evidence, so it is not counted in the rates above."
        )
    if args.only_ids is None:
        report = score(items, predictions, spec, predictor.name)
        (args.out / "report.md").write_text(
            render_markdown(report, spec, [note]), encoding="utf-8"
        )
    else:
        # Items picked by id are chosen, not sampled — every rate over them
        # would describe the choosing, not the set.
        (args.out / "report.md").write_text(
            f"# Re-run of {len(items)} chosen items: `{predictor.name}` on "
            f"`{args.items.name}`\n\n"
            "No scores are reported here. These items were selected by id rather than "
            "sampled, so precision, recall and every rate over them would describe the "
            "selection. See predictions.jsonl for the verdicts.\n\n"
            f"{note}\n",
            encoding="utf-8",
        )
    (args.out / "run.json").write_text(json.dumps({
        "items_file": str(args.items),
        "items": len(items),
        "predictor": predictor.name,
        "model": model,
        "concurrency": args.concurrency,
        "only_ids": str(args.only_ids) if args.only_ids else None,
        "finished_at": datetime.now(UTC).isoformat(),
        "quotes_checked": predictor.quotes_checked,
        "malformed_entries": predictor.malformed_entries,
        "missing_verdicts": predictor.missing_verdicts,
        # None, not 0, when nothing was checked: unmeasured is not clean.
        "malformed_rate": rate,
        "max_malformed_rate": threshold,
        "prediction_errors": errors,
        "prediction_error_rate": error_rate,
        "missing_set_verdicts": (
            predictor.missing_set_verdicts
            if isinstance(predictor, GradedAuditPredictor)
            else None
        ),
        "recovered_set_verdicts": (
            predictor.recovered_set_verdicts
            if isinstance(predictor, GradedAuditPredictor)
            else None
        ),
        "input_tokens": provider.input_tokens,
        "output_tokens": provider.output_tokens,
    }, indent=2) + "\n", encoding="utf-8")

    scored = "re-run" if args.only_ids is not None else "scored"
    print(f"{len(items)} items {scored} with {model} ({predictor.name}); wrote {args.out}")
    print(note)
    print(f"tokens used: {provider.input_tokens} input, {provider.output_tokens} output")
    over = [
        f"{label} {value:.1%}"
        for label, value in (("unusable-response rate", rate), ("item failure rate", error_rate))
        if value is not None and value > threshold
    ]
    if over:
        # Everything above is written first: the run was paid for, and its
        # results stay on disk to be inspected. The failure is about whether
        # the scores may be quoted, not about keeping the data.
        print(
            f"FAILED: {' and '.join(over)} above the {threshold:.1%} threshold; "
            "the scores in this run describe partly unjudged evidence.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run(_parser().parse_args())))


if __name__ == "__main__":
    main()
