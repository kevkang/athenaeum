"""Set A1, model-free part: labeled citations built from SciFact.

SciFact (Wadden et al., 2020, "Fact or Fiction: Verifying Scientific Claims")
pairs expert-written scientific claims with sentences of a cited abstract
annotated as supporting or contradicting each claim. Its data is released by
AllenAI under CC BY-NC 2.0, so attribution travels with any set built from it.

SciFact annotates evidence as *rationale sets*: the sentences in one set are
the evidence for the claim taken together, and one abstract can carry several
independent sets. This module keeps that structure rather than flattening it.
Every sentence of a set becomes its own nested quote on the item, the way an
Athenaeum statement carries several citations, and each set becomes its own
item rather than being merged with the others.

Items built here have labels that follow mechanically from SciFact's own
annotations, with no model involved:

- supported: a set annotated SUPPORT, quoted in full.
- misread: a set annotated CONTRADICT. Real contradictions found by
  annotators, not synthetic corruptions.
- differs_from_source: a claim and its cited abstract, with the quotes swapped
  for a set from a different abstract, so the words are not in the cited
  source. The source check confirms that for every quote.

Overreach and sign-flipped misreads need a claim rewritten; a model drafts
those and a person reviews them (drafts.py). `secondhand` is left null
throughout: SciFact rationales almost never contain citation markers (1 of 773
labeled pairs), so this set cannot test the flag.

One claim-abstract pair never contributes to more than one verdict, and no
rationale set is used twice, so items never disagree with each other about the
same evidence. A fixed seed makes the set reproducible.

    python -m athenaeum.evaluation.sets.scifact --data-dir <scifact>/data \\
        --out eval/audit/sets/set-a1-scifact.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from athenaeum.domain.audit_verdicts import DIFFERS_FROM_SOURCE, MISREAD, SUPPORTED
from athenaeum.domain.source_check import DIFFERS_FROM_SOURCE as QUOTE_DIFFERS
from athenaeum.domain.source_check import check_quote
from athenaeum.evaluation.items import EvalItem, item_to_json

SOURCE = "set-a1-scifact"
DEFAULT_SEED = 20260915


@dataclass(frozen=True)
class RationaleSet:
    """One claim, one cited abstract, and one annotated set of evidence
    sentences from it. `quotes` holds those sentences in document order, each
    one a separate quote."""

    claim_id: int
    claim: str
    doc_id: str
    label: str  # "SUPPORT" or "CONTRADICT"
    set_index: int  # which annotated set this is, within the claim-abstract pair
    sentence_indices: tuple[int, ...]
    quotes: tuple[str, ...]
    abstract: tuple[str, ...]

    @property
    def pair(self) -> tuple[int, str]:
        return (self.claim_id, self.doc_id)


def _read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_sets(data_dir: Path, splits: tuple[str, ...] = ("train", "dev")) -> list[RationaleSet]:
    """Every annotated rationale set in `splits`, in file order.

    All of a set's sentences are kept, adjacent or not: they are the evidence
    only together, so quoting a subset would misread the annotation. The test
    split is skipped by default because its labels are not released.
    """
    corpus = {
        str(d["doc_id"]): tuple(d["abstract"]) for d in _read_jsonl(data_dir / "corpus.jsonl")
    }
    sets: list[RationaleSet] = []
    for split in splits:
        for claim in _read_jsonl(data_dir / f"claims_{split}.jsonl"):
            for doc_id, rationales in (claim.get("evidence") or {}).items():
                abstract = corpus[str(doc_id)]
                for set_index, rationale in enumerate(rationales):
                    indices = tuple(sorted(rationale["sentences"]))
                    if not indices:
                        continue
                    sets.append(RationaleSet(
                        claim_id=claim["id"],
                        claim=claim["claim"],
                        doc_id=str(doc_id),
                        label=rationale["label"],
                        set_index=set_index,
                        sentence_indices=indices,
                        quotes=tuple(abstract[i] for i in indices),
                        abstract=abstract,
                    ))
    return sets


def _item(
    rationale: RationaleSet,
    verdict: str,
    quotes: tuple[str, ...],
    construction: str,
    **meta: object,
) -> EvalItem:
    return EvalItem(
        id=f"scifact-{rationale.claim_id}-{rationale.doc_id}-set{rationale.set_index}-{construction}",
        claim=rationale.claim,
        quotes=quotes,
        true_verdict=verdict,
        source=SOURCE,
        source_text=rationale.abstract,
        meta={
            "scifact_claim_id": rationale.claim_id,
            "scifact_doc_id": rationale.doc_id,
            "rationale_set_index": rationale.set_index,
            "rationale_sentence_indices": list(rationale.sentence_indices),
            "quote_count": len(quotes),
            "construction": construction,
            **meta,
        },
    )


def _absent_from(quotes: Sequence[str], abstract: Sequence[str]) -> bool:
    """True when none of `quotes` appears in `abstract`, according to the same
    source check production uses. Sentence ids are placeholders: the check
    needs them only to report where a match is."""
    sentences = [(uuid.UUID(int=i), s) for i, s in enumerate(abstract)]
    return all(check_quote(q, sentences).state == QUOTE_DIFFERS for q in quotes)


def build_items(
    sets: Sequence[RationaleSet], per_verdict: int, seed: int = DEFAULT_SEED
) -> list[EvalItem]:
    """`per_verdict` items each of supported, misread and differs_from_source.

    Sets are shuffled with `seed`, then taken in order: SUPPORT sets feed
    supported first and then differs_from_source, CONTRADICT sets feed misread.
    A claim-abstract pair is claimed by the first verdict that uses it and
    skipped by the others, so no pair appears under two verdicts, and no set is
    used twice.

    A differs_from_source item keeps its claim and cited abstract but borrows
    every quote from a donor set belonging to a different abstract, and the
    donor is skipped unless the source check agrees none of the borrowed words
    are in the cited abstract. No donor is borrowed twice; otherwise the first
    eligible donor wins every time and these items all share one quote.
    """
    rng = random.Random(seed)
    support = [s for s in sets if s.label == "SUPPORT"]
    contradict = [s for s in sets if s.label == "CONTRADICT"]
    rng.shuffle(support)
    rng.shuffle(contradict)

    owner: dict[tuple[int, str], str] = {}

    def take(verdict: str, pool: Sequence[RationaleSet]) -> list[RationaleSet]:
        chosen: list[RationaleSet] = []
        for candidate in pool:
            if len(chosen) == per_verdict:
                break
            if owner.setdefault(candidate.pair, verdict) != verdict:
                continue
            chosen.append(candidate)
        if len(chosen) < per_verdict:
            raise ValueError(
                f"need {per_verdict} rationale sets for {verdict}, found {len(chosen)}"
            )
        return chosen

    supported_sets = take(SUPPORTED, support)
    swapped_sets = take(DIFFERS_FROM_SOURCE, support)
    misread_sets = take(MISREAD, contradict)

    items = [_item(s, SUPPORTED, s.quotes, "support") for s in supported_sets]
    items += [_item(s, MISREAD, s.quotes, "contradict") for s in misread_sets]

    donors = list(sets)
    rng.shuffle(donors)
    # Keyed on the quote text, not the donor's identity: two different sets can
    # hold the same sentence, and borrowing it twice would give two items the
    # same evidence.
    borrowed: set[tuple[str, ...]] = set()
    for target in swapped_sets:
        donor = next(
            (
                d for d in donors
                if d.quotes not in borrowed
                and d.doc_id != target.doc_id
                and _absent_from(d.quotes, target.abstract)
            ),
            None,
        )
        if donor is None:
            raise ValueError(f"no unused donor set absent from abstract {target.doc_id}")
        borrowed.add(donor.quotes)
        items.append(_item(
            target,
            DIFFERS_FROM_SOURCE,
            donor.quotes,
            "swapped_quotes",
            donor_doc_id=donor.doc_id,
            donor_claim_id=donor.claim_id,
        ))
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir", required=True, type=Path, help="SciFact release data/ directory"
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--per-verdict", type=int, default=100)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    items = build_items(load_sets(args.data_dir), args.per_verdict, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(item_to_json(i) + "\n" for i in items), encoding="utf-8")
    print(f"wrote {len(items)} items to {args.out}")


if __name__ == "__main__":
    main()
