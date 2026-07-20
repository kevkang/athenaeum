"""Round-trip tests for the .ath archive format (no database required)."""
import uuid

import pytest

from athenaeum.archive.export import ATH_VERSION, dumps, loads

_PROJECT_ID = str(uuid.uuid4())
_PAPER_ID = str(uuid.uuid4())
_STATEMENT_ID = str(uuid.uuid4())
_STATEMENT2_ID = str(uuid.uuid4())
_PASSAGE_ID = str(uuid.uuid4())
_SYNTHESIS_ID = str(uuid.uuid4())

SAMPLE: dict = {
    "ath_version": ATH_VERSION,
    "exported_at": "2026-05-17T00:00:00+00:00",
    "project": {
        "id": _PROJECT_ID,
        "title": "Round-trip Test Project",
        "description": "Automated archive test",
        "license": "CC-BY-4.0",
    },
    "papers": [
        {
            "id": _PAPER_ID,
            "doi": "10.1234/roundtrip",
            "openalex_id": "W9999999",
            "title": "A Test Paper on Round-Trips",
            "authors_json": [{"name": "Alice Author", "orcid": None}],
            "venue": "Journal of Tests",
            "year": 2024,
            "abstract": "We test things thoroughly.",
            "status": "full_text",
            "created_at": "2026-05-17T00:00:00+00:00",
        }
    ],
    "passages": [
        {
            "id": _PASSAGE_ID,
            "paper_id": _PAPER_ID,
            "sentence_ids": [str(uuid.uuid4())],
            "verbatim_text": "We test things thoroughly.",
            "verified": True,
            "verified_at": "2026-05-17T01:00:00+00:00",
            "checker_model": "claude-opus-4-7",
        }
    ],
    "syntheses": [
        {
            "id": _SYNTHESIS_ID,
            "claim_text": "Systematic testing prevents regressions.",
            "synthesis_text": "The evidence strongly supports the claim.",
            "retrieval_corpus": "project",
            "model": "claude-opus-4-7",
            "checker_model": "claude-opus-4-7",
            "verification_rate": 1.0,
            "status": "done",
            "run_at": "2026-05-17T01:00:00+00:00",
            "query_expansion_json": {"synonyms": ["testing", "validation"]},
            "evidence": [
                {"passage_id": _PASSAGE_ID, "role": "theory", "ord": 0}
            ],
        }
    ],
    "statements": [
        {
            "id": _STATEMENT_ID,
            "type": "foundation",
            "status": "locked",
            "assertion": "Systematic testing prevents regressions.",
            "built_from_synthesis_id": _SYNTHESIS_ID,
            "locked_at": "2026-05-17T02:00:00+00:00",
            "drafted_at": "2026-05-17T01:30:00+00:00",
            "evidence": [
                {"target_kind": "paper", "target_id": _PAPER_ID, "role": "theory"}
            ],
            "edges": [],
        },
        {
            "id": _STATEMENT2_ID,
            "type": "synthesis",
            "status": "draft",
            "assertion": "Round-trips are essential for data integrity.",
            "built_from_synthesis_id": None,
            "locked_at": None,
            "drafted_at": "2026-05-17T02:00:00+00:00",
            "evidence": [],
            "edges": [
                {"to_statement_id": _STATEMENT_ID, "relation": "supports"}
            ],
        },
    ],
    "annotations": [
        {
            "id": str(uuid.uuid4()),
            "author_id": str(uuid.uuid4()),
            "target_kind": "statement",
            "target_id": _STATEMENT_ID,
            "body": "Strong foundation statement.",
            "created_at": "2026-05-17T03:00:00+00:00",
        }
    ],
    "evaluations": [
        {
            "id": str(uuid.uuid4()),
            "synthesis_id": _SYNTHESIS_ID,
            "passage_id": _PASSAGE_ID,
            "user_id": str(uuid.uuid4()),
            "stance": "agree",
            "body": "Verified empirically.",
            "created_at": "2026-05-17T03:00:00+00:00",
        }
    ],
    "datasets": [],
    "provenance": [
        {
            "id": str(uuid.uuid4()),
            "kind": "statement_locked",
            "payload_json": {"statement_id": _STATEMENT_ID},
            "created_at": "2026-05-17T02:00:00+00:00",
        }
    ],
}


def test_yaml_roundtrip() -> None:
    yaml_str = dumps(SAMPLE)
    restored = loads(yaml_str)

    assert restored["ath_version"] == ATH_VERSION
    assert restored["project"]["title"] == SAMPLE["project"]["title"]
    assert restored["project"]["id"] == _PROJECT_ID

    assert len(restored["papers"]) == 1
    assert restored["papers"][0]["doi"] == "10.1234/roundtrip"
    assert restored["papers"][0]["year"] == 2024

    assert len(restored["passages"]) == 1
    assert restored["passages"][0]["verified"] is True
    assert restored["passages"][0]["verbatim_text"] == "We test things thoroughly."

    assert len(restored["syntheses"]) == 1
    s = restored["syntheses"][0]
    assert s["verification_rate"] == 1.0
    assert s["synthesis_text"] == "The evidence strongly supports the claim."
    assert s["evidence"][0]["role"] == "theory"

    assert len(restored["statements"]) == 2
    s0 = next(s for s in restored["statements"] if s["id"] == _STATEMENT_ID)
    assert s0["status"] == "locked"
    assert s0["evidence"][0]["target_kind"] == "paper"
    assert s0["edges"] == []

    s1 = next(s for s in restored["statements"] if s["id"] == _STATEMENT2_ID)
    assert s1["built_from_synthesis_id"] is None
    assert s1["edges"][0]["to_statement_id"] == _STATEMENT_ID
    assert s1["edges"][0]["relation"] == "supports"


def test_required_top_level_keys() -> None:
    yaml_str = dumps(SAMPLE)
    restored = loads(yaml_str)
    required = {
        "ath_version", "exported_at", "project", "papers",
        "passages", "syntheses", "statements", "annotations",
        "evaluations", "datasets", "provenance",
    }
    assert required.issubset(set(restored.keys()))


def test_null_fields_survive_roundtrip() -> None:
    yaml_str = dumps(SAMPLE)
    restored = loads(yaml_str)
    s1 = next(s for s in restored["statements"] if s["id"] == _STATEMENT2_ID)
    assert s1["built_from_synthesis_id"] is None
    assert s1["locked_at"] is None


def test_nested_json_survives_roundtrip() -> None:
    yaml_str = dumps(SAMPLE)
    restored = loads(yaml_str)
    synth = restored["syntheses"][0]
    assert synth["query_expansion_json"]["synonyms"] == ["testing", "validation"]
    prov = restored["provenance"][0]
    assert prov["payload_json"]["statement_id"] == _STATEMENT_ID


def test_yaml_is_human_readable() -> None:
    yaml_str = dumps(SAMPLE)
    # Should contain readable field names, not binary/base64
    assert "ath_version:" in yaml_str
    assert "Round-trip Test Project" in yaml_str
    assert "locked" in yaml_str
    assert "10.1234/roundtrip" in yaml_str
