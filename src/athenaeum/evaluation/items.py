"""Labeled citations for evaluating Audit, and how they are stored.

A labeled set is a JSONL file: one JSON object per line, so a set can be
diffed and reviewed line by line and appended to without rewriting it.

    {"id": "scifact-12-4983-set0-support", "claim": "...",
     "quotes": ["...", "..."], "true_verdict": "supported",
     "source": "set-a1-scifact"}

`quotes` is a list because one citation's evidence can be several passages,
the way an Athenaeum statement carries several citations and SciFact annotates
a rationale as a set of sentences that are evidence *together*. Each passage is
kept separate rather than concatenated, so nothing implies the source contains
them as one continuous run of text.

`true_verdict` is the label a person, or a known corruption, assigned.
`secondhand` is a separate yes/no flag, not a verdict, and is null when a set
doesn't label it. `source` names the set an item belongs to: results are always
reported per set and never pooled, because sets built differently (synthetic
corruptions versus real mistakes) measure different things. `source_text`, when
present, is the cited source's text as a list of sentences; it lets the
deterministic source check decide `differs_from_source` the way production does,
instead of leaving that to the model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REQUIRED_TEXT = ("id", "claim", "true_verdict", "source")
_OPTIONAL = ("secondhand", "source_text", "meta")


@dataclass(frozen=True)
class EvalItem:
    id: str
    claim: str
    # One or more quoted passages, in the order they appear in the source.
    quotes: tuple[str, ...]
    true_verdict: str
    source: str
    secondhand: bool | None = None
    source_text: tuple[str, ...] | None = None
    # Free-form provenance (original dataset ids, the corruption applied,
    # labeler ids). Recorded for traceability; never used in scoring.
    meta: dict[str, Any] = field(default_factory=dict)


def item_to_json(item: EvalItem) -> str:
    """One JSONL line for an item, with keys sorted so a regenerated set diffs
    cleanly against the previous one. Unset optional fields are omitted."""
    obj: dict[str, Any] = {
        "id": item.id,
        "claim": item.claim,
        "quotes": list(item.quotes),
        "true_verdict": item.true_verdict,
        "source": item.source,
    }
    if item.secondhand is not None:
        obj["secondhand"] = item.secondhand
    if item.source_text is not None:
        obj["source_text"] = list(item.source_text)
    if item.meta:
        obj["meta"] = item.meta
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def parse_item(obj: Any, where: str = "item") -> EvalItem:
    """Validate one decoded JSON object as an EvalItem.

    Unknown fields are an error rather than ignored, so a misspelled field
    (say "true_verdit") fails loudly instead of quietly dropping a label.
    `where` names the location (file and line) in error messages.
    """
    if not isinstance(obj, dict):
        raise ValueError(f"{where}: expected a JSON object")
    unknown = set(obj) - set(_REQUIRED_TEXT) - {"quotes"} - set(_OPTIONAL)
    if unknown:
        raise ValueError(f"{where}: unknown fields {sorted(unknown)}")
    for key in _REQUIRED_TEXT:
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where}: {key!r} must be a non-empty string")
    quotes = obj.get("quotes")
    if (
        not isinstance(quotes, list)
        or not quotes
        or not all(isinstance(q, str) and q.strip() for q in quotes)
    ):
        raise ValueError(f"{where}: 'quotes' must be a non-empty list of non-empty strings")
    secondhand = obj.get("secondhand")
    if secondhand is not None and not isinstance(secondhand, bool):
        raise ValueError(f"{where}: 'secondhand' must be true, false, or null")
    source_text = obj.get("source_text")
    if source_text is not None and (
        not isinstance(source_text, list)
        or not all(isinstance(s, str) and s.strip() for s in source_text)
    ):
        raise ValueError(f"{where}: 'source_text' must be a list of non-empty strings")
    meta = obj.get("meta", {})
    if not isinstance(meta, dict):
        raise ValueError(f"{where}: 'meta' must be an object")
    return EvalItem(
        id=obj["id"],
        claim=obj["claim"],
        quotes=tuple(quotes),
        true_verdict=obj["true_verdict"],
        source=obj["source"],
        secondhand=secondhand,
        source_text=tuple(source_text) if source_text is not None else None,
        meta=meta,
    )


def load_items(path: Path) -> list[EvalItem]:
    """Read a JSONL set, skipping blank lines. Invalid JSON, an invalid item,
    or a repeated id raises ValueError naming the file and line."""
    items: list[EvalItem] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            where = f"{path.name}:{line_number}"
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{where}: invalid JSON ({exc.msg})") from exc
            item = parse_item(obj, where)
            if item.id in seen:
                raise ValueError(f"{where}: duplicate id {item.id!r}")
            seen.add(item.id)
            items.append(item)
    return items
