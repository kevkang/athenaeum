"""Shared helper for deriving a paper's first-author display name from its
authors_json — used anywhere a citation-style label is rendered."""
from __future__ import annotations

from typing import Any


def first_author_name(authors_json: Any) -> str | None:
    if isinstance(authors_json, list) and authors_json:
        first = authors_json[0]
        if isinstance(first, dict):
            return first.get("name") or first.get("display_name")
        if isinstance(first, str):
            return first
    if isinstance(authors_json, dict):
        names = authors_json.get("names")
        if isinstance(names, list) and names:
            return names[0]
    return None
