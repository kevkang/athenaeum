"""Set A1, model-drafted part: overreach and flipped-claim misread items.

Two corruptions from the plan need a claim rewritten, which string rules can't
do reliably:

- overreach: the claim is broadened (a qualifier removed, the scope or
  population generalized, or a magnitude inflated), so the evidence that
  supported the original now supports only a narrower claim.
- misread, flipped: the claim's central finding is reversed, so the same
  evidence now contradicts it.

A model drafts each rewrite and a person decides whether it does what it
should. Only accepted drafts become items, so every label in this part of the
set has been checked by a human. The work is two commands:

    draft      writes a review sheet (CSV; opens in any spreadsheet)
    assemble   turns the reviewed sheet into a JSONL set of accepted rows

Drafts come from SciFact SUPPORT rationale sets whose claims the model-free
set doesn't use, one set per claim, so no claim appears under two corruptions.
A set's sentences are evidence only together, so the whole set travels with the
item as separate nested quotes, exactly as in scifact.py.

Draft with a different model from the one being evaluated: a model rewriting
claims in the ways it would itself misjudge them could make the set
systematically easy or hard for that model.

    python -m athenaeum.evaluation.sets.drafts draft --data-dir <scifact>/data \\
        --exclude-set eval/audit/sets/set-a1-scifact.jsonl \\
        --out eval/audit/review/set-a1-drafts.csv
    python -m athenaeum.evaluation.sets.drafts assemble --data-dir <scifact>/data \\
        --sheet eval/audit/review/set-a1-drafts.csv \\
        --out eval/audit/sets/set-a1-scifact-drafted.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import random
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from athenaeum.config import get_settings
from athenaeum.domain.audit_verdicts import MISREAD, OVERREACH
from athenaeum.evaluation.items import EvalItem, item_to_json, load_items
from athenaeum.evaluation.sets.scifact import DEFAULT_SEED, SOURCE, RationaleSet, load_sets
from athenaeum.provider.anthropic_client import AnthropicProvider
from athenaeum.provider.base import Message, Provider

# How a set's quotes are shown to the model and in the review sheet: one per
# line, so a reviewer sees the evidence exactly as the item carries it.
QUOTE_SEPARATOR: Final = "\n"


@dataclass(frozen=True)
class Corruption:
    construction: str  # recorded in item ids and meta
    verdict: str
    instruction: str


BROADENED = Corruption(
    construction="broadened_claim",
    verdict=OVERREACH,
    instruction=(
        "Rewrite the claim so it says clearly more than the evidence supports, using exactly "
        "one change: remove a qualifier or condition, generalize the population or scope, or "
        "increase a magnitude. The change must go clearly beyond what the evidence shows, not "
        "to a value or scope the evidence nearly reaches. The rewritten claim must stay on the "
        "same topic and must not contradict the evidence; the evidence should support only "
        "the narrower original."
    ),
)

FLIPPED = Corruption(
    construction="flipped_claim",
    verdict=MISREAD,
    instruction=(
        "Rewrite the claim so its central finding points the opposite way (for example, "
        "'increases' becomes 'decreases', or 'higher' becomes 'lower'), changing as few words "
        "as possible and nothing about its scope. Prefer reversing the direction over adding "
        "'not': a claim that something 'does not' have an effect can still agree with "
        "evidence whose effect was small or uncertain. The evidence should clearly contradict "
        "the rewritten claim."
    ),
)

CORRUPTIONS: Final = {c.construction: c for c in (BROADENED, FLIPPED)}

_SYSTEM = (
    "You help build a test set for a citation checker by rewriting scientific claims in a "
    "specific, controlled way. You are shown a claim and the evidence quoted for it, which "
    "supports the claim as written. Follow the rewrite instruction exactly and keep every "
    "other part of the claim unchanged."
)

_TOOL: Final[dict[str, Any]] = {
    "name": "rewrite_claim",
    "description": "Return the rewritten claim and a few words on what was changed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rewritten_claim": {"type": "string"},
            "change": {"type": "string", "description": "What was changed, in a few words."},
        },
        "required": ["rewritten_claim", "change"],
    },
}

REVIEW_COLUMNS: Final = (
    "id", "intended_verdict", "quotes", "original_claim", "drafted_claim", "change",
    "decision", "note",
)
ACCEPT: Final = "accept"
REJECT: Final = "reject"


def select_sets(
    sets: Sequence[RationaleSet],
    used_claim_ids: set[int],
    per_corruption: int,
    seed: int = DEFAULT_SEED,
) -> dict[str, list[RationaleSet]]:
    """Pick `per_corruption` SUPPORT rationale sets for each corruption.

    Claims in `used_claim_ids` (those in the model-free set) are skipped, only
    one set per claim is considered, and the corruptions get disjoint sets.
    """
    seen: set[int] = set()
    candidates: list[RationaleSet] = []
    for rationale in sets:
        if rationale.label == "SUPPORT" and rationale.claim_id not in used_claim_ids | seen:
            seen.add(rationale.claim_id)
            candidates.append(rationale)
    random.Random(seed).shuffle(candidates)
    needed = per_corruption * len(CORRUPTIONS)
    if len(candidates) < needed:
        raise ValueError(f"need {needed} unused SUPPORT claims, have {len(candidates)}")
    return {
        name: candidates[i * per_corruption : (i + 1) * per_corruption]
        for i, name in enumerate(CORRUPTIONS)
    }


@dataclass(frozen=True)
class Draft:
    rationale: RationaleSet
    corruption: Corruption
    drafted_claim: str
    change: str

    @property
    def id(self) -> str:
        r = self.rationale
        return f"scifact-{r.claim_id}-{r.doc_id}-set{r.set_index}-{self.corruption.construction}"

    @property
    def quotes_text(self) -> str:
        return QUOTE_SEPARATOR.join(self.rationale.quotes)


async def draft_one(
    provider: Provider, rationale: RationaleSet, corruption: Corruption
) -> Draft | None:
    """Ask the model for one rewrite. None if it returns nothing usable,
    including a "rewrite" identical to the original claim."""
    user_message = (
        f"REWRITE INSTRUCTION:\n{corruption.instruction}\n\n"
        f"CLAIM:\n{rationale.claim}\n\n"
        f"EVIDENCE:\n{QUOTE_SEPARATOR.join(rationale.quotes)}"
    )
    _, tool_result = await provider.complete(
        messages=[Message(role="user", content=user_message)],
        system=_SYSTEM,
        max_tokens=512,
        tools=[_TOOL],
    )
    if tool_result is None:
        return None
    rewritten = str(tool_result["input"].get("rewritten_claim") or "").strip()
    if not rewritten or rewritten == rationale.claim.strip():
        return None
    return Draft(rationale, corruption, rewritten, str(tool_result["input"].get("change") or ""))


async def draft_all(
    provider: Provider, selections: dict[str, list[RationaleSet]], concurrency: int = 4
) -> tuple[list[Draft], int]:
    """Draft every selected set, at most `concurrency` calls at once. Returns
    the usable drafts in selection order and how many attempts failed."""
    semaphore = asyncio.Semaphore(concurrency)
    jobs = [
        (rationale, CORRUPTIONS[name])
        for name, chosen in selections.items()
        for rationale in chosen
    ]

    async def run(rationale: RationaleSet, corruption: Corruption) -> Draft | None:
        async with semaphore:
            return await draft_one(provider, rationale, corruption)

    results = await asyncio.gather(*(run(r, c) for r, c in jobs))
    drafts = [d for d in results if d is not None]
    return drafts, len(results) - len(drafts)


def write_review_sheet(drafts: Sequence[Draft], path: Path) -> None:
    """One row per draft, with empty decision and note columns for the reviewer."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for d in drafts:
            writer.writerow({
                "id": d.id,
                "intended_verdict": d.corruption.verdict,
                "quotes": d.quotes_text,
                "original_claim": d.rationale.claim,
                "drafted_claim": d.drafted_claim,
                "change": d.change,
                "decision": "",
                "note": "",
            })


def assemble(sheet: Path, sets: Sequence[RationaleSet]) -> list[EvalItem]:
    """Items for the accepted rows of a reviewed sheet.

    Every row needs a decision of accept or reject: a blank one means the review
    isn't finished, and skipping it would shrink the set without anyone
    deciding. A reviewer may edit `drafted_claim` before accepting, and that
    edit is kept. The quotes must be left alone: they are checked against
    SciFact, since accidentally edited evidence would no longer be what the
    label was decided against.
    """
    by_key = {(r.claim_id, r.doc_id, r.set_index): r for r in sets}
    items: list[EvalItem] = []
    with sheet.open(newline="", encoding="utf-8") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            where = f"{sheet.name}:{line_number}"
            decision = (row.get("decision") or "").strip().lower()
            if decision not in (ACCEPT, REJECT):
                raise ValueError(
                    f"{where}: decision must be 'accept' or 'reject', got {row.get('decision')!r}"
                )
            if decision == REJECT:
                continue
            _prefix, claim_id, doc_id, set_part, construction = row["id"].split("-", 4)
            rationale = by_key[(int(claim_id), doc_id, int(set_part.removeprefix("set")))]
            if row["quotes"] != QUOTE_SEPARATOR.join(rationale.quotes):
                raise ValueError(f"{where}: the quotes were edited; only drafted_claim may change")
            corruption = CORRUPTIONS[construction]
            items.append(EvalItem(
                id=row["id"],
                claim=row["drafted_claim"].strip(),
                quotes=rationale.quotes,
                true_verdict=corruption.verdict,
                source=SOURCE,
                source_text=rationale.abstract,
                meta={
                    "scifact_claim_id": rationale.claim_id,
                    "scifact_doc_id": rationale.doc_id,
                    "rationale_set_index": rationale.set_index,
                    "quote_count": len(rationale.quotes),
                    "construction": construction,
                    "original_claim": rationale.claim,
                    "change": row.get("change") or "",
                    "review_note": row.get("note") or "",
                },
            ))
    return items


# ── Command line ─────────────────────────────────────────────────────────────


async def _draft(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set; every draft needs a model call.", file=sys.stderr)
        return 2
    model = args.model or settings.synthesis_model
    if model == settings.checker_model:
        print(
            f"warning: drafting with {model}, the same model Audit uses to judge citations",
            file=sys.stderr,
        )
    used = {int(item.meta["scifact_claim_id"]) for item in load_items(args.exclude_set)}
    selections = select_sets(load_sets(args.data_dir), used, args.per_corruption, args.seed)
    provider = AnthropicProvider(settings.anthropic_api_key, model)
    drafts, failed = await draft_all(provider, selections, args.concurrency)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_review_sheet(drafts, args.out)
    print(f"{len(drafts)} drafts written to {args.out} ({failed} failed), drafted with {model}")
    print(f"tokens used: {provider.input_tokens} input, {provider.output_tokens} output")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    draft = commands.add_parser("draft", help="write a review sheet of model-drafted rewrites")
    draft.add_argument("--data-dir", required=True, type=Path)
    draft.add_argument(
        "--exclude-set", required=True, type=Path,
        help="the model-free set; its claims are not reused",
    )
    draft.add_argument("--out", required=True, type=Path, help="review sheet to write (CSV)")
    draft.add_argument("--per-corruption", type=int, default=100)
    draft.add_argument("--seed", type=int, default=DEFAULT_SEED)
    draft.add_argument(
        "--model", help="drafting model; defaults to the synthesis model, not Audit's checker"
    )
    draft.add_argument("--concurrency", type=int, default=4)

    build = commands.add_parser("assemble", help="build a JSONL set from a reviewed sheet")
    build.add_argument("--data-dir", required=True, type=Path)
    build.add_argument("--sheet", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "draft":
        sys.exit(asyncio.run(_draft(args)))

    items = assemble(args.sheet, load_sets(args.data_dir))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(item_to_json(i) + "\n" for i in items), encoding="utf-8")
    counts = Counter(i.true_verdict for i in items)
    print(f"wrote {len(items)} accepted items to {args.out}: {dict(counts)}")


if __name__ == "__main__":
    main()
