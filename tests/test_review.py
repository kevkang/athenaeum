"""Tests for the blind review sheet and its reveal.

What matters here is what the reviewer cannot see. If a label, an Audit
verdict, or even the group an item came from leaks into the sheet, the review
stops being independent evidence and becomes an echo of the thing it was meant
to check.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from athenaeum.evaluation.items import EvalItem
from athenaeum.evaluation.review import (
    CONTRADICT_AGREEMENT,
    DISAGREEMENT,
    SHEET_COLUMNS,
    SUPPORT_AGREEMENT,
    Completed,
    load_completed,
    reveal,
    select,
    write_key,
    write_sheet,
)


def _items() -> list[EvalItem]:
    """Ten supported, ten contradict-derived, three swapped."""
    made: list[EvalItem] = []
    for n in range(10):
        made.append(EvalItem(
            id=f"sup-{n}", claim=f"Claim {n}.", quotes=(f"Quote {n}.",),
            true_verdict="supported", source="set-a1", source_text=(f"Abstract {n}.",),
            meta={"construction": "support"},
        ))
    for n in range(10):
        made.append(EvalItem(
            id=f"con-{n}", claim=f"Claim c{n}.", quotes=(f"Quote c{n}.",),
            true_verdict="misread", source="set-a1", source_text=(f"Abstract c{n}.",),
            meta={"construction": "contradict"},
        ))
    for n in range(3):
        made.append(EvalItem(
            id=f"swap-{n}", claim=f"Claim s{n}.", quotes=(f"Quote s{n}.",),
            true_verdict="differs_from_source", source="set-a1",
            meta={"construction": "swapped_quotes"},
        ))
    return made


def _baseline(items: list[EvalItem], supported_ids: set[str]) -> dict[str, dict]:
    return {
        i.id: {
            "item_id": i.id,
            "verdict": "supported" if i.id in supported_ids else "not_supported",
            "rationale": "because",
        }
        for i in items
    }


# The first four supported items are called supported by Audit; the rest of
# the supported ones are disagreements.
_AGREED = {"sup-0", "sup-1", "sup-2", "sup-3"}


def test_every_disagreement_is_reviewed_and_agreements_are_sampled() -> None:
    items = _items()
    review, population = select(items, _baseline(items, _AGREED), 1, 2, 3)

    strata = [r.stratum for r in review]
    assert strata.count(DISAGREEMENT) == 6
    assert strata.count(SUPPORT_AGREEMENT) == 2
    assert strata.count(CONTRADICT_AGREEMENT) == 3
    # The set-level sizes the reveal needs to reweight by.
    assert population == {
        DISAGREEMENT: 6, SUPPORT_AGREEMENT: 4, CONTRADICT_AGREEMENT: 10,
    }


def test_swapped_quote_items_are_never_put_to_a_person() -> None:
    """Whether a quote's words are in the paper is the source check's job."""
    items = _items()
    review, _ = select(items, _baseline(items, _AGREED), 1, 2, 3)

    assert not any(r.item_id.startswith("swap-") for r in review)


def test_the_same_seed_picks_the_same_items_and_a_different_one_does_not() -> None:
    items = _items()
    baseline = _baseline(items, _AGREED)

    first, _ = select(items, baseline, 7, 2, 3)
    again, _ = select(items, baseline, 7, 2, 3)
    other, _ = select(items, baseline, 8, 2, 3)

    assert [r.item_id for r in first] == [r.item_id for r in again]
    assert [r.item_id for r in first] != [r.item_id for r in other]


def test_review_ids_are_assigned_after_the_shuffle() -> None:
    """If ids were assigned before shuffling, r001 onward would run through
    the disagreements first and the group would be readable off the id."""
    items = _items()
    review, _ = select(items, _baseline(items, _AGREED), 3, 2, 3)

    strata = [r.stratum for r in review]
    assert strata != sorted(strata, key=lambda s: s != DISAGREEMENT)


def test_asking_for_more_agreements_than_exist_fails_loudly() -> None:
    items = _items()

    with pytest.raises(ValueError, match="only 4 exist"):
        select(items, _baseline(items, _AGREED), 1, 99, 3)


def test_the_sheet_carries_no_label_no_verdict_and_no_group(tmp_path: Path) -> None:
    items = _items()
    review, _ = select(items, _baseline(items, _AGREED), 1, 2, 3)
    path = tmp_path / "sheet.csv"

    write_sheet(review, {i.id: i for i in items}, path)
    text = path.read_text(encoding="utf-8")

    assert text.splitlines()[0] == ",".join(SHEET_COLUMNS)
    for leak in ("supported", "misread", "not_supported", DISAGREEMENT, SUPPORT_AGREEMENT):
        assert leak not in text
    # The item's own id would give the label away: it ends in -support.
    assert "sup-" not in text and "con-" not in text


def test_the_sheet_shows_the_claim_the_quotes_and_the_abstract(tmp_path: Path) -> None:
    items = _items()
    review, _ = select(items, _baseline(items, _AGREED), 1, 2, 3)
    path = tmp_path / "sheet.csv"

    write_sheet(review, {i.id: i for i in items}, path)
    text = path.read_text(encoding="utf-8")

    first = review[0]
    item = {i.id: i for i in items}[first.item_id]
    assert item.claim in text
    assert item.quotes[0] in text
    assert (item.source_text or ("",))[0] in text


# ── Reading the finished sheet ───────────────────────────────────────────────


def _prepare(tmp_path: Path, verdicts: dict[str, str]) -> tuple[Path, Path, list]:
    items = _items()
    review, population = select(items, _baseline(items, _AGREED), 1, 2, 3)
    sheet, key = tmp_path / "sheet.csv", tmp_path / "key.json"
    write_sheet(review, {i.id: i for i in items}, sheet)
    write_key(review, population, 1, key)

    with sheet.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["verdict"] = verdicts.get(row["review_id"], "supported")
        row["note"] = "a note"
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SHEET_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return sheet, key, review


def test_a_finished_sheet_reads_back_against_the_key(tmp_path: Path) -> None:
    sheet, key, review = _prepare(tmp_path, {})

    completed = load_completed(sheet, key)

    assert len(completed) == len(review)
    assert {c.review.review_id for c in completed} == {r.review_id for r in review}


def _rewrite_first_verdict(sheet: Path, verdict: str) -> None:
    with sheet.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["verdict"] = verdict
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SHEET_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def test_an_unfinished_sheet_is_refused_rather_than_scored(tmp_path: Path) -> None:
    sheet, key, _ = _prepare(tmp_path, {})
    _rewrite_first_verdict(sheet, "")

    with pytest.raises(ValueError, match="1 rows have no verdict"):
        load_completed(sheet, key)


def test_a_verdict_outside_the_taxonomy_is_refused(tmp_path: Path) -> None:
    sheet, key, _ = _prepare(tmp_path, {})
    _rewrite_first_verdict(sheet, "looks fine")

    with pytest.raises(ValueError, match="outside the taxonomy"):
        load_completed(sheet, key)


# ── Reveal ───────────────────────────────────────────────────────────────────


def test_the_reveal_separates_label_errors_from_audit_errors(tmp_path: Path) -> None:
    sheet, key, _ = _prepare(tmp_path, {})
    completed = load_completed(sheet, key)
    population = json.loads(key.read_text(encoding="utf-8"))["stratum_sizes_in_set"]

    text = reveal(completed, population)

    # Everyone was marked `supported`, so every contradict-derived item is a
    # label the reviewer rejected, and every disagreement is an Audit error.
    assert "## 4. Items where the reviewer differs from the set's label" in text
    assert "## 5. Items where Audit differs from the reviewer, label intact" in text
    assert "3 of 11" in text  # the three contradict items the reviewer called supported


def test_the_reveal_gives_intervals_on_small_groups(tmp_path: Path) -> None:
    sheet, key, _ = _prepare(tmp_path, {})
    completed = load_completed(sheet, key)
    population = json.loads(key.read_text(encoding="utf-8"))["stratum_sizes_in_set"]

    text = reveal(completed, population)

    assert "95% CI" in text


def test_the_reveal_states_the_single_reviewer_limit(tmp_path: Path) -> None:
    """A number from one reviewer must never travel without it."""
    sheet, key, _ = _prepare(tmp_path, {})
    completed = load_completed(sheet, key)

    text = reveal(completed, {})

    assert "no agreement statistic" in text


def test_completed_rows_keep_the_original_label_beside_the_new_one(tmp_path: Path) -> None:
    sheet, key, _ = _prepare(tmp_path, {})

    completed: list[Completed] = load_completed(sheet, key)

    assert all(c.review.source_verdict for c in completed)
    assert all(c.review.source_label in ("SUPPORT", "CONTRADICT") for c in completed)
