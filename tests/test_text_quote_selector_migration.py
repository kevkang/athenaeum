"""Migration 0031 carries a frozen copy of the selector logic, per this
project's convention that a migration produces the same result forever. This
checks the copy still matches the live function, so a transcription slip when
the migration was written cannot hide — and so that if the live function later
changes on purpose, this test says loudly that backfilled rows were anchored by
the older rules.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest

from athenaeum.domain.source_check import text_quote_selector as live

_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations" / "versions" / "0031_text_quote_selectors.py"
)


def _frozen():
    spec = importlib.util.spec_from_file_location("migration_0031", _PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.text_quote_selector


_A, _B, _C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

CASES = [
    ("fix carbon", [(_A, "These bacteria fix carbon at depth.")], frozenset()),
    ("efflux rose sharply", [(_A, "Soil ef- flux rose sharply after warming.")], frozenset()),
    ('the "hot spot" at 2-4 cm',
     [(_A, "They found the \u201chot spot\u201d at 2\u20134 cm depth.")], frozenset()),
    ("CO2 flux", [(_A, "Measured CO\u2082 flux doubled.")], frozenset()),
    ("depth. Tubeworms rely",
     [(_A, "These bacteria fix carbon at depth."), (_B, "Tubeworms rely on this.")], frozenset()),
    ("n = 12 cores",
     [(_A, "In control plots, n = 12 cores."), (_B, "In warmed plots, n = 12 cores.")],
     frozenset({_B})),
    ("absent words", [(_A, "Nothing like that here.")], frozenset()),
    ("soft\u00adhyphen", [(_C, "A soft\u00adhyphen hides in this sentence.")], frozenset()),
    ("anything", [], frozenset()),
]


@pytest.mark.parametrize(("quote", "sentences", "prefer"), CASES)
def test_frozen_copy_matches_the_live_function(quote, sentences, prefer) -> None:
    assert _frozen()(quote, sentences, prefer) == live(quote, sentences, prefer)
