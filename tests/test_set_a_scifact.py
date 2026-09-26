"""Tests for building Set A1's model-free items from SciFact-format data.

A tiny SciFact-shaped dataset is written to a temporary directory, so the
builder runs exactly as it would on the real release. Its fixture deliberately
includes an abstract with two rationale sets and one set whose sentences are
not adjacent, since those are the two shapes that the earlier builder got
wrong. Every label is checked with the same source check production uses,
rather than trusting the builder.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from athenaeum.domain.source_check import DIFFERS_FROM_SOURCE as QUOTE_DIFFERS
from athenaeum.domain.source_check import MATCHES_SOURCE, check_quote
from athenaeum.evaluation.items import item_to_json, load_items
from athenaeum.evaluation.sets.scifact import SOURCE, build_items, load_sets

ABSTRACTS = {
    1: ["Warming raised soil respiration by 28%.", "Plots were sampled monthly."],
    2: ["Nitrogen addition reduced root biomass.", "Effects persisted for three years."],
    3: ["Drought suppressed microbial activity.", "Recovery took two seasons."],
    # Two rationale sets point here, and one of them is non-adjacent.
    4: [
        "Grazing increased compaction.",
        "Cattle were excluded from controls.",
        "Infiltration fell.",
    ],
    5: ["Fire reduced litter cover.", "Erosion rose after burning."],
    6: ["Liming raised soil pH.", "Earthworm counts doubled."],
}

# claim id, claim, doc id, label, rationale sets (each a list of sentence indices)
CLAIMS = [
    (1, "Warming increases soil respiration.", 1, "SUPPORT", [[0]]),
    (2, "Nitrogen addition lowers root biomass.", 2, "SUPPORT", [[0]]),
    (3, "Drought suppresses microbes.", 3, "SUPPORT", [[0]]),
    (4, "Grazing compacts soil and cuts infiltration.", 4, "SUPPORT", [[0, 2], [0]]),
    (5, "Fire increases litter cover.", 5, "CONTRADICT", [[0]]),
    (6, "Liming lowers soil pH.", 6, "CONTRADICT", [[0]]),
]


def _write_scifact(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    with (data / "corpus.jsonl").open("w") as handle:
        for doc_id, sentences in ABSTRACTS.items():
            handle.write(json.dumps({"doc_id": doc_id, "title": "t", "abstract": sentences}) + "\n")
    with (data / "claims_train.jsonl").open("w") as handle:
        for claim_id, claim, doc_id, label, rationales in CLAIMS:
            evidence = {
                str(doc_id): [{"sentences": s, "label": label} for s in rationales]
            }
            handle.write(json.dumps({"id": claim_id, "claim": claim, "evidence": evidence}) + "\n")
        handle.write(json.dumps({"id": 99, "claim": "Unlabeled.", "evidence": {}}) + "\n")
    (data / "claims_dev.jsonl").write_text("")
    return data


def test_every_rationale_set_becomes_its_own_entry(tmp_path: Path) -> None:
    sets = load_sets(_write_scifact(tmp_path))

    assert len(sets) == 7  # six claims, one of which has two rationale sets
    grazing = sorted((s for s in sets if s.claim_id == 4), key=lambda s: s.set_index)
    assert [s.sentence_indices for s in grazing] == [(0, 2), (0,)]


def test_a_non_adjacent_set_keeps_every_sentence_as_a_separate_quote(tmp_path: Path) -> None:
    """The sentences are evidence only together, so none may be dropped, and
    they must not be concatenated into text the abstract never contains."""
    sets = load_sets(_write_scifact(tmp_path))

    gapped = next(s for s in sets if s.sentence_indices == (0, 2))

    assert gapped.quotes == ("Grazing increased compaction.", "Infiltration fell.")


def test_builds_the_requested_count_of_each_verdict(tmp_path: Path) -> None:
    items = build_items(load_sets(_write_scifact(tmp_path)), per_verdict=2, seed=1)

    counts: dict[str, int] = {}
    for item in items:
        counts[item.true_verdict] = counts.get(item.true_verdict, 0) + 1
    assert counts == {"supported": 2, "misread": 2, "differs_from_source": 2}
    assert all(item.source == SOURCE and item.secondhand is None for item in items)


def test_a_claim_abstract_pair_never_appears_under_two_verdicts(tmp_path: Path) -> None:
    items = build_items(load_sets(_write_scifact(tmp_path)), per_verdict=2, seed=1)

    owner: dict[tuple[int, str], str] = {}
    for item in items:
        key = (item.meta["scifact_claim_id"], item.meta["scifact_doc_id"])
        owner.setdefault(key, item.true_verdict)
        assert owner[key] == item.true_verdict


def test_every_label_is_true_according_to_the_source_check(tmp_path: Path) -> None:
    items = build_items(load_sets(_write_scifact(tmp_path)), per_verdict=2, seed=1)

    for item in items:
        assert item.source_text is not None
        sentences = [(uuid.UUID(int=i), s) for i, s in enumerate(item.source_text)]
        expected = QUOTE_DIFFERS if item.true_verdict == "differs_from_source" else MATCHES_SOURCE
        for quote in item.quotes:
            assert check_quote(quote, sentences).state == expected, item.id


def test_every_swapped_item_borrows_a_different_donor(tmp_path: Path) -> None:
    """Regression from the pilot: every swapped item once borrowed the same
    donor quote, which made the category trivially easy."""
    items = build_items(load_sets(_write_scifact(tmp_path)), per_verdict=2, seed=1)

    swapped = [item.quotes for item in items if item.true_verdict == "differs_from_source"]

    assert len(swapped) == len(set(swapped)) == 2


def test_same_seed_gives_the_same_set_and_it_round_trips_through_jsonl(tmp_path: Path) -> None:
    sets = load_sets(_write_scifact(tmp_path))
    first = build_items(sets, per_verdict=2, seed=7)
    second = build_items(sets, per_verdict=2, seed=7)
    assert [item_to_json(i) for i in first] == [item_to_json(i) for i in second]

    path = tmp_path / "set.jsonl"
    path.write_text("".join(item_to_json(i) + "\n" for i in first), encoding="utf-8")
    assert load_items(path) == first


def test_asking_for_more_items_than_the_data_holds_fails_with_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="need 3 rationale sets"):
        build_items(load_sets(_write_scifact(tmp_path)), per_verdict=3)
