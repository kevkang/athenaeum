"""Tests for the model-drafted part of Set A1: choosing rationale sets,
drafting, and turning a human-reviewed sheet into labeled items.

The model is faked. What matters here is the workflow around it: no claim is
reused, failed drafts are dropped rather than guessed at, a set's quotes travel
with the item intact, and only rows a person explicitly accepted become labels.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from athenaeum.evaluation.sets.drafts import (
    BROADENED,
    FLIPPED,
    QUOTE_SEPARATOR,
    Draft,
    assemble,
    draft_all,
    select_sets,
    write_review_sheet,
)
from athenaeum.evaluation.sets.scifact import SOURCE, load_sets
from athenaeum.provider.base import Message
from tests.test_set_a_scifact import _write_scifact


class _RewritingProvider:
    """Prefixes the claim according to which instruction it was given, or
    returns nothing / an unchanged claim for chosen claims."""

    def __init__(self, fail_on: str = "", unchanged_on: str = "") -> None:
        self.fail_on = fail_on
        self.unchanged_on = unchanged_on
        self.last_evidence: str | None = None

    async def complete(self, messages: list[Message], system, max_tokens=4096, tools=None):
        text = messages[0].content
        claim = text.split("CLAIM:\n", 1)[1].split("\n\nEVIDENCE:", 1)[0]
        self.last_evidence = text.split("EVIDENCE:\n", 1)[1]
        if self.fail_on and self.fail_on in claim:
            return None, None
        if self.unchanged_on and self.unchanged_on in claim:
            rewritten = claim
        else:
            rewritten = ("Everywhere, " if "narrower original" in text else "Not: ") + claim
        tool_input = {"rewritten_claim": rewritten, "change": "x"}
        return None, {"name": "rewrite_claim", "input": tool_input}


def _sets(tmp_path: Path):
    return load_sets(_write_scifact(tmp_path))


# ── Choosing rationale sets ──────────────────────────────────────────────────

def test_select_sets_skips_used_claims_and_keeps_corruptions_disjoint(tmp_path: Path) -> None:
    selections = select_sets(_sets(tmp_path), used_claim_ids={1, 2}, per_corruption=1, seed=3)

    chosen = [s.claim_id for group in selections.values() for s in group]
    assert set(selections) == {BROADENED.construction, FLIPPED.construction}
    assert len(chosen) == len(set(chosen)) == 2
    assert not {1, 2} & set(chosen)
    assert all(s.label == "SUPPORT" for group in selections.values() for s in group)


def test_select_sets_fails_when_too_few_unused_claims(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="need 4 unused SUPPORT claims, have 2"):
        select_sets(_sets(tmp_path), used_claim_ids={1, 2}, per_corruption=2)


# ── Drafting ─────────────────────────────────────────────────────────────────

async def test_the_model_is_shown_every_quote_in_the_set(tmp_path: Path) -> None:
    sets = _sets(tmp_path)
    multi = next(s for s in sets if len(s.quotes) > 1)
    provider = _RewritingProvider()

    await draft_all(provider, {BROADENED.construction: [multi]})

    assert provider.last_evidence == QUOTE_SEPARATOR.join(multi.quotes)


async def test_failed_and_unchanged_drafts_are_dropped_and_counted(tmp_path: Path) -> None:
    selections = select_sets(_sets(tmp_path), used_claim_ids=set(), per_corruption=2, seed=3)
    claims = [s.claim for group in selections.values() for s in group]
    provider = _RewritingProvider(fail_on=claims[0], unchanged_on=claims[1])

    drafts, failed = await draft_all(provider, selections)

    assert failed == 2
    assert len(drafts) == 2
    for d in drafts:
        prefix = "Everywhere, " if d.corruption is BROADENED else "Not: "
        assert d.drafted_claim == prefix + d.rationale.claim


# ── Review sheet ─────────────────────────────────────────────────────────────

def _reviewed_sheet(tmp_path: Path, drafts: list[Draft], decisions: list[str], **edits) -> Path:
    sheet = tmp_path / "review.csv"
    write_review_sheet(drafts, sheet)
    with sheet.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row, decision in zip(rows, decisions, strict=True):
        row["decision"] = decision
        row.update(edits)
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return sheet


async def _drafts(tmp_path: Path) -> tuple[list, list[Draft]]:
    sets = _sets(tmp_path)
    selections = select_sets(sets, used_claim_ids=set(), per_corruption=1, seed=3)
    drafts, _failed = await draft_all(_RewritingProvider(), selections)
    return sets, drafts


async def test_assemble_keeps_only_accepted_rows_with_the_intended_labels(tmp_path: Path) -> None:
    sets, drafts = await _drafts(tmp_path)
    sheet = _reviewed_sheet(tmp_path, drafts, ["accept", "Reject"])

    items = assemble(sheet, sets)

    [item] = items
    accepted = drafts[0]
    assert item.id == accepted.id
    assert item.true_verdict == accepted.corruption.verdict
    assert item.claim == accepted.drafted_claim
    assert item.quotes == accepted.rationale.quotes
    assert item.source == SOURCE and item.source_text == accepted.rationale.abstract
    assert item.meta["original_claim"] == accepted.rationale.claim


async def test_assemble_keeps_a_reviewers_edit_to_the_drafted_claim(tmp_path: Path) -> None:
    sets, drafts = await _drafts(tmp_path)
    sheet = _reviewed_sheet(tmp_path, drafts, ["accept", "accept"], drafted_claim="Edited claim.")

    assert {i.claim for i in assemble(sheet, sets)} == {"Edited claim."}


async def test_assemble_refuses_an_unfinished_review(tmp_path: Path) -> None:
    sets, drafts = await _drafts(tmp_path)
    sheet = _reviewed_sheet(tmp_path, drafts, ["accept", ""])

    with pytest.raises(ValueError, match=r"review\.csv:3: decision must be"):
        assemble(sheet, sets)


async def test_assemble_refuses_edited_quotes(tmp_path: Path) -> None:
    sets, drafts = await _drafts(tmp_path)
    sheet = _reviewed_sheet(tmp_path, drafts, ["accept", "accept"], quotes="Tampered quote.")

    with pytest.raises(ValueError, match="quotes were edited"):
        assemble(sheet, sets)
