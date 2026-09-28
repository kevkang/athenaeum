"""Blind human review of a labeled set, and the three-way reveal afterwards.

    python -m athenaeum.evaluation.review build --items <set.jsonl> \\
        --baseline <binary run dir> --out <review dir>
    ... the reviewer fills in sheet.csv ...
    python -m athenaeum.evaluation.review reveal --dir <review dir>

Why this exists: when Audit and a SciFact label disagree, the run alone cannot
say which of them is wrong. A person reading the claim and the quotes with no
knowledge of either answer can. The same person also reads items the two
*agreed* on, because two answers agreeing is not evidence that either is right.

Blind means blind: the sheet carries the claim, the quotes and the cited
abstract, and nothing else. No label, no Audit verdict, no stratum, and review
ids are assigned after shuffling, so their order says nothing either. Everything
withheld lives in key.json, which is written once and not needed until the
reveal.

One reviewer, decided 2026-09-16. The consequence travels with every number
this produces: with a single reviewer there is no agreement statistic, so the
corrected labels carry an unmeasured error of their own. It is smaller than the
one they replace — SciFact's labels were assigned for a different task — but it
is not zero, and no result from this review may be stated as if it were.

The three strata are sampled at different rates, so they are reported
separately. A set-level estimate is still possible afterwards, because each
stratum's size in the full set is recorded in key.json: weight each stratum's
rate by its share of the set. Nothing here does that arithmetic, because it
would mean printing a set-level rate before anyone has reviewed anything.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from athenaeum.domain.audit_verdicts import DEFINITIONS, SUPPORTED, VERDICTS
from athenaeum.evaluation.items import EvalItem, load_items
from athenaeum.evaluation.metrics import Proportion

# What the reviewer sees, in this order. Nothing here distinguishes a
# disagreement from an agreement.
SHEET_COLUMNS = ("review_id", "claim", "quotes", "source_abstract", "verdict", "note", "secondhand")

DISAGREEMENT = "disagreement"
SUPPORT_AGREEMENT = "support_agreement"
CONTRADICT_AGREEMENT = "contradict_agreement"
STRATA = (DISAGREEMENT, SUPPORT_AGREEMENT, CONTRADICT_AGREEMENT)


@dataclass(frozen=True)
class ReviewItem:
    """One item as the reveal needs it: what the reviewer saw, and everything
    that was withheld while they saw it."""

    review_id: str
    item_id: str
    stratum: str
    # The label the set carries, and where it came from. Kept so a corrected
    # label never silently replaces the original: both are on record.
    source_label: str
    source_verdict: str
    # What the binary run said, and why.
    audit_verdict: str
    audit_rationale: str


def _construction(item: EvalItem) -> str:
    """How the item was built: support, contradict, or swapped_quotes."""
    return str(item.meta.get("construction", ""))


def select(
    items: Sequence[EvalItem],
    baseline: dict[str, dict],
    seed: int,
    support_agreements: int,
    contradict_agreements: int,
) -> tuple[list[ReviewItem], dict[str, int]]:
    """Pick the review set: every disagreement, plus samples of each kind of
    agreement, shuffled into one indistinguishable list.

    Swapped-quote items are deliberately left out. Whether a quote's words
    appear in the cited paper is decided by the deterministic source check, so
    asking a person to judge it would measure their patience, not the labels.

    Returns the review items and each stratum's size in the full set, which is
    what a later set-level estimate would need to reweight by.
    """
    rng = random.Random(seed)

    def audit_said_supported(item: EvalItem) -> bool:
        return baseline[item.id]["verdict"] == SUPPORTED

    supported_items = [i for i in items if _construction(i) == "support"]
    contradict_items = [i for i in items if _construction(i) == "contradict"]
    disagreements = [i for i in supported_items if not audit_said_supported(i)]
    support_agreed = [i for i in supported_items if audit_said_supported(i)]
    contradict_agreed = [i for i in contradict_items if not audit_said_supported(i)]

    population = {
        DISAGREEMENT: len(disagreements),
        SUPPORT_AGREEMENT: len(support_agreed),
        CONTRADICT_AGREEMENT: len(contradict_agreed),
    }
    # Sorted before sampling so the same seed gives the same sample whatever
    # order the file happened to be in.
    chosen: list[tuple[EvalItem, str]] = [(i, DISAGREEMENT) for i in disagreements]
    for group, stratum, n in (
        (support_agreed, SUPPORT_AGREEMENT, support_agreements),
        (contradict_agreed, CONTRADICT_AGREEMENT, contradict_agreements),
    ):
        pool = sorted(group, key=lambda i: i.id)
        if n > len(pool):
            raise ValueError(f"asked for {n} {stratum} items, only {len(pool)} exist")
        chosen += [(i, stratum) for i in rng.sample(pool, n)]

    rng.shuffle(chosen)
    review_items = [
        ReviewItem(
            review_id=f"r{n:03d}",
            item_id=item.id,
            stratum=stratum,
            source_label="SUPPORT" if _construction(item) == "support" else "CONTRADICT",
            source_verdict=item.true_verdict,
            audit_verdict=baseline[item.id]["verdict"],
            audit_rationale=baseline[item.id].get("rationale", ""),
        )
        # Numbered only after the shuffle, so a review id carries no signal.
        for n, (item, stratum) in enumerate(chosen, start=1)
    ]
    return review_items, population


def _quote_block(item: EvalItem) -> str:
    if len(item.quotes) == 1:
        return item.quotes[0]
    return "\n\n".join(f"[{n}] {q}" for n, q in enumerate(item.quotes, start=1))


def write_sheet(review_items: Sequence[ReviewItem], items: dict[str, EvalItem], path: Path) -> None:
    """The reviewer's file: claim, quotes, abstract, and three columns to fill."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SHEET_COLUMNS)
        for review in review_items:
            item = items[review.item_id]
            writer.writerow([
                review.review_id,
                item.claim,
                _quote_block(item),
                " ".join(item.source_text or ()),
                "", "", "",
            ])


def write_key(
    review_items: Sequence[ReviewItem], population: dict[str, int], seed: int, path: Path
) -> None:
    path.write_text(
        json.dumps(
            {
                "seed": seed,
                "stratum_sizes_in_set": population,
                "items": [asdict(r) for r in review_items],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_readme(review_items: Sequence[ReviewItem], path: Path) -> None:
    counts = Counter(r.stratum for r in review_items)
    verdicts = "\n".join(f"- `{name}` — {DEFINITIONS[name]}" for name in VERDICTS)
    path.write_text(
        f"""# Blind review

{len(review_items)} items in `sheet.csv`. Each row gives a claim, the quote or
quotes cited for it, and the cited paper's abstract. Judge whether the quoted
evidence, taken together, supports the claim as written.

Fill in three columns:

- `verdict` — one of the names below, exactly as spelled.
- `note` — a sentence on why, especially where the call was close.
- `secondhand` — `yes` if the quoted passage is itself citing another work for
  this point, otherwise leave blank.

## Verdicts

{verdicts}

## Ground rules

- Do not open `key.json` until every row has a verdict. It holds the existing
  labels and what Audit said, and reading any of it ends the blind review.
- Judge the quote against the claim, not against what you know to be true of
  the subject. A correct claim can still be wrongly cited.
- `indeterminate` is a real answer. Use it rather than guessing.

Then run:

    python -m athenaeum.evaluation.review reveal --dir {path.parent}

## What this review can and cannot establish

One reviewer, so there is no second reading to measure agreement against. The
corrected labels are better evidence than the originals, which were assigned
for a different task, but they carry an error of their own that nothing here
measures. Every number derived from them has to say so.

The three groups of items were drawn at different rates and are reported
separately. Nothing about the set as a whole follows from any one of them
without reweighting by the group sizes recorded in `key.json`.

Group sizes here: """
        + ", ".join(f"{k} {counts[k]}" for k in STRATA if counts[k])
        + " (which group an item is in is not in `sheet.csv`, and is not "
        "recoverable from its review id).\n",
        encoding="utf-8",
    )


# ── Reveal ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Completed:
    review: ReviewItem
    verdict: str
    note: str
    secondhand: bool | None


def load_completed(sheet_path: Path, key_path: Path) -> list[Completed]:
    """Read the filled sheet against the key, refusing anything unfinished.

    A missing or misspelled verdict is an error rather than a skipped row: a
    review that silently loses items would quietly change every denominator.
    """
    key = json.loads(key_path.read_text(encoding="utf-8"))
    by_review_id = {r["review_id"]: ReviewItem(**r) for r in key["items"]}

    with sheet_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    completed: list[Completed] = []
    blank: list[str] = []
    unknown: list[str] = []
    for row in rows:
        review_id = (row.get("review_id") or "").strip()
        if review_id not in by_review_id:
            raise ValueError(f"sheet row {review_id!r} is not in the key")
        verdict = (row.get("verdict") or "").strip()
        if not verdict:
            blank.append(review_id)
            continue
        if verdict not in VERDICTS:
            unknown.append(f"{review_id}={verdict!r}")
            continue
        raw_secondhand = (row.get("secondhand") or "").strip().lower()
        completed.append(Completed(
            review=by_review_id[review_id],
            verdict=verdict,
            note=(row.get("note") or "").strip(),
            secondhand=True if raw_secondhand in ("yes", "y", "true") else None,
        ))
    if blank:
        raise ValueError(f"{len(blank)} rows have no verdict yet, e.g. {blank[:3]}")
    if unknown:
        raise ValueError(f"{len(unknown)} rows have a verdict outside the taxonomy: {unknown[:3]}")
    missing = set(by_review_id) - {c.review.review_id for c in completed}
    if missing:
        raise ValueError(f"{len(missing)} key items are absent from the sheet")
    return completed


def _rate_line(label: str, p: Proportion) -> str:
    if p.rate is None:
        return f"{label}: no items."
    body = f"{label}: {p.count} of {p.n} ({p.rate:.1%})"
    if p.n < 100 and p.interval is not None:
        body += f", 95% CI {p.interval.low:.1%} to {p.interval.high:.1%}"
    return body + "."


def reveal(completed: Sequence[Completed], population: dict[str, int]) -> str:
    """The three-way comparison, per stratum, as Markdown."""
    lines = [
        "# Blind review: reviewer, set label, Audit",
        "",
        f"{len(completed)} items reviewed by one reviewer, blind to both the set's labels "
        "and Audit's verdicts.",
        "",
        "One reviewer means no agreement statistic: the corrected labels below carry an "
        "error of their own that this review does not measure.",
        "",
        "## 1. Where the reviewer landed, by group",
        "",
        "| group | reviewed | of that group in the set | "
        + " | ".join(f"`{v}`" for v in VERDICTS) + " |",
        "|---" * (len(VERDICTS) + 3) + "|",
    ]
    for stratum in STRATA:
        group = [c for c in completed if c.review.stratum == stratum]
        if not group:
            continue
        counts = Counter(c.verdict for c in group)
        cells = " | ".join(str(counts[v]) for v in VERDICTS)
        lines.append(
            f"| {stratum} | {len(group)} | {population.get(stratum, '?')} | {cells} |"
        )

    lines += [
        "",
        "## 2. Does the reviewer's reading match the set's label?",
        "",
        "The set's labels came from SciFact, assigned for a different task. Where the "
        "reviewer differs, the label is the thing in question.",
        "",
    ]
    for stratum in STRATA:
        group = [c for c in completed if c.review.stratum == stratum]
        if not group:
            continue
        agreed = sum(1 for c in group if c.verdict == c.review.source_verdict)
        lines.append("- " + _rate_line(
            f"{stratum}: reviewer gives the set's own label",
            Proportion(agreed, len(group)),
        ))

    lines += [
        "",
        "## 3. Audit against the reviewer",
        "",
        "Audit answered supported or not supported, so the reviewer's verdict is read the "
        "same way: `supported` against everything else.",
        "",
        "| group | Audit agrees with reviewer | items | rate | 95% interval |",
        "|---|---|---|---|---|",
    ]
    for stratum in STRATA:
        group = [c for c in completed if c.review.stratum == stratum]
        if not group:
            continue
        agreed = sum(
            1 for c in group
            if (c.review.audit_verdict == SUPPORTED) == (c.verdict == SUPPORTED)
        )
        p = Proportion(agreed, len(group))
        interval = (
            f"{p.interval.low:.1%} to {p.interval.high:.1%}" if p.interval is not None else "n/a"
        )
        rate = f"{p.rate:.1%}" if p.rate is not None else "n/a"
        lines.append(f"| {stratum} | {agreed} | {len(group)} | {rate} | {interval} |")

    # The two ways the original disagreement can resolve, item by item.
    label_errors = [
        c for c in completed if c.verdict != c.review.source_verdict
    ]
    audit_errors = [
        c for c in completed
        if c.verdict == c.review.source_verdict
        and (c.review.audit_verdict == SUPPORTED) != (c.verdict == SUPPORTED)
    ]
    lines += [
        "",
        "## 4. Items where the reviewer differs from the set's label",
        "",
        f"{len(label_errors)} of {len(completed)}. The set's label is on the left, the "
        "reviewer's on the right.",
        "",
        "| review id | item | set label | reviewer | note |",
        "|---|---|---|---|---|",
    ]
    for c in label_errors:
        note = c.note.replace("|", "/")
        lines.append(
            f"| {c.review.review_id} | `{c.review.item_id}` | `{c.review.source_verdict}` | "
            f"`{c.verdict}` | {note} |"
        )
    lines += [
        "",
        "## 5. Items where Audit differs from the reviewer, label intact",
        "",
        f"{len(audit_errors)} of {len(completed)}. These are Audit's own errors: the "
        "reviewer read the citation the same way the set does, and Audit did not.",
        "",
        "| review id | item | reviewer | Audit | Audit's rationale |",
        "|---|---|---|---|---|",
    ]
    for c in audit_errors:
        rationale = c.review.audit_rationale.replace("|", "/")[:200]
        lines.append(
            f"| {c.review.review_id} | `{c.review.item_id}` | `{c.verdict}` | "
            f"`{c.review.audit_verdict}` | {rationale} |"
        )
    return "\n".join(lines) + "\n"


# ── Command line ─────────────────────────────────────────────────────────────


def _build(args: argparse.Namespace) -> int:
    items = load_items(args.items)
    baseline = {
        json.loads(line)["item_id"]: json.loads(line)
        for line in (args.baseline / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    missing = [i.id for i in items if i.id not in baseline]
    if missing:
        print(f"{len(missing)} items have no baseline prediction", file=sys.stderr)
        return 2

    review_items, population = select(
        items, baseline, args.seed, args.support_agreements, args.contradict_agreements
    )
    args.out.mkdir(parents=True, exist_ok=True)
    write_sheet(review_items, {i.id: i for i in items}, args.out / "sheet.csv")
    write_key(review_items, population, args.seed, args.out / "key.json")
    write_readme(review_items, args.out / "README.md")

    counts = Counter(r.stratum for r in review_items)
    print(f"{len(review_items)} items to review in {args.out / 'sheet.csv'}")
    for stratum in STRATA:
        print(f"  {stratum}: {counts[stratum]} of {population[stratum]} in the set")
    print(f"seed {args.seed}; labels and Audit verdicts are in key.json — leave it closed")
    return 0


def _reveal(args: argparse.Namespace) -> int:
    key = json.loads((args.dir / "key.json").read_text(encoding="utf-8"))
    completed = load_completed(args.dir / "sheet.csv", args.dir / "key.json")
    text = reveal(completed, key["stratum_sizes_in_set"])
    (args.dir / "reveal.md").write_text(text, encoding="utf-8")
    print(f"wrote {args.dir / 'reveal.md'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="write a blind review sheet")
    build.add_argument("--items", required=True, type=Path)
    build.add_argument("--baseline", required=True, type=Path, help="binary run directory")
    build.add_argument("--out", required=True, type=Path)
    build.add_argument("--seed", type=int, default=20260926, help="fixes which items are sampled")
    build.add_argument("--support-agreements", type=int, default=35)
    build.add_argument("--contradict-agreements", type=int, default=20)
    build.set_defaults(func=_build)

    rev = sub.add_parser("reveal", help="compare the finished sheet against labels and Audit")
    rev.add_argument("--dir", required=True, type=Path)
    rev.set_defaults(func=_reveal)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
