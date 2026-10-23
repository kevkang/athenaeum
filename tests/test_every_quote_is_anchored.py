"""Every code path that creates a quote must give it a selector.

text_quote_selector is nullable on purpose — a quote not found in this server's
copy gets none — so a new creation path that simply forgets would leave NULLs
without any error. passages.source_check solves the same problem by being NOT
NULL with no default; this column can't, so this test does the job statically
instead: it reads the source and fails if any Passage(...) or Highlight(...)
construction neither passes text_quote_selector= nor sits in a function that
anchors it via check_passage / repoint_highlight.

It exists because the first sweep for these sites missed three of them.
"""
from __future__ import annotations

import ast
import pathlib

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "athenaeum"
_MODELS = {"Passage", "Highlight"}
_ANCHORING_CALLS = {"check_passage", "repoint_highlight"}


def _called_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _construction_sites() -> list[tuple[pathlib.Path, int, bool]]:
    """(file, line, anchored) for every Passage/Highlight construction."""
    sites: list[tuple[pathlib.Path, int, bool]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
            anchors_later = any(_called_name(c) in _ANCHORING_CALLS for c in calls)
            for call in calls:
                if _called_name(call) not in _MODELS:
                    continue
                passes_selector = any(k.arg == "text_quote_selector" for k in call.keywords)
                sites.append((path, call.lineno, passes_selector or anchors_later))
    return sites


def test_the_scan_actually_finds_the_known_sites() -> None:
    """Guard the guard: if the scan found nothing it would pass vacuously."""
    files = {path.name for path, _line, _ok in _construction_sites()}

    assert {"orchestrator.py", "backtrace.py", "highlights.py",
            "zotero_highlights.py", "import_.py"} <= files


def test_every_quote_construction_is_anchored() -> None:
    unanchored = [
        f"{path.relative_to(_SRC.parent.parent)}:{line}"
        for path, line, anchored in _construction_sites()
        if not anchored
    ]

    assert not unanchored, (
        "These create a Passage or Highlight without a text_quote_selector: "
        + ", ".join(unanchored)
        + ". Pass text_quote_selector= (see source_check.selector_for_new_quote) "
        "or anchor it with check_passage / repoint_highlight."
    )
