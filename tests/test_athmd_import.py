"""Tests for `.athmd` import — same-project reconciliation only (see
archive/athmd.py's module docstring): a resolved athm:statement/<uuid>
recreates the mark, an unresolved one degrades to plain text with a
warning. Also the full round-trip: build_athmd -> import_athmd should
reproduce the original document's text/marks (not its literal JSON, since
mark order isn't semantically meaningful and citation markers/References
are a cached rendering, never re-imported)."""
from __future__ import annotations

import io
import uuid

from fastapi import UploadFile

from athenaeum.api.compose import import_compose_document
from athenaeum.archive.athmd import build_athmd, import_athmd
from athenaeum.storage.models import ComposeDocument, Statement
from tests.test_citations import _seed_project


def _flatten(content_json: dict) -> list[tuple]:
    """Canonical form for comparing two ProseMirror docs semantically:
    mark order within a text node isn't meaningful, so this normalizes each
    text run to (text, sorted-mark-types, statementId-or-None)."""
    blocks = []
    for block in content_json.get("content") or []:
        runs = []
        for node in block.get("content") or []:
            marks = node.get("marks") or []
            mark_types = sorted(m["type"] for m in marks if m["type"] != "statementLink")
            statement_id = next(
                (m["attrs"]["statementId"] for m in marks if m["type"] == "statementLink"), None
            )
            runs.append((node.get("text", ""), tuple(mark_types), statement_id))
        level = (block.get("attrs") or {}).get("level")
        blocks.append((block["type"], level, tuple(runs)))
    return blocks


async def test_import_athmd_resolves_known_statement_link(db_session) -> None:
    owner, project = await _seed_project(db_session)
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="a resolvable claim",
    )
    db_session.add(statement)
    await db_session.flush()

    text = f"[a resolvable claim](athm:statement/{statement.id})\n"
    result = await import_athmd(db_session, text, project.id)

    assert result["warnings"] == []
    para = result["content_json"]["content"][0]
    assert para["type"] == "paragraph"
    node = para["content"][0]
    assert node["text"] == "a resolvable claim"
    assert node["marks"] == [{"type": "statementLink", "attrs": {"statementId": str(statement.id)}}]


async def test_import_athmd_degrades_unresolved_statement_link(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    missing_id = str(uuid.uuid4())

    text = f"[an orphaned claim](athm:statement/{missing_id})\n"
    result = await import_athmd(db_session, text, project.id)

    assert result["warnings"] == [
        {"span_text": "an orphaned claim", "missing_statement_id": missing_id}
    ]
    para = result["content_json"]["content"][0]
    node = para["content"][0]
    assert node["text"] == "an orphaned claim"
    assert "marks" not in node


async def test_import_athmd_strips_trailing_citation_markers(db_session) -> None:
    owner, project = await _seed_project(db_session)
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="widely observed",
    )
    db_session.add(statement)
    await db_session.flush()

    text = f"[widely observed](athm:statement/{statement.id})[^1.1], [^2.1], and more.\n"
    result = await import_athmd(db_session, text, project.id)

    para = result["content_json"]["content"][0]
    texts = [n["text"] for n in para["content"]]
    assert texts == ["widely observed", ", and more."]


async def test_import_athmd_preserves_bold_and_italic(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    text = "Plain **bold** and *italic* and ***both***.\n"

    result = await import_athmd(db_session, text, project.id)

    para = result["content_json"]["content"][0]
    runs = [(n["text"], sorted(m["type"] for m in n.get("marks", []))) for n in para["content"]]
    assert ("bold", ["bold"]) in runs
    assert ("italic", ["italic"]) in runs
    assert ("both", ["bold", "italic"]) in runs


async def test_import_athmd_parses_inline_and_block_math(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    text = (
        "Inline math: $E = mc^2$.\n\n"
        "$$\n"
        r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}"
        "\n$$\n"
    )

    result = await import_athmd(db_session, text, project.id)

    content = result["content_json"]["content"]
    assert content[0]["type"] == "paragraph"
    inline_math = next(n for n in content[0]["content"] if n["type"] == "inlineMath")
    assert inline_math["attrs"]["latex"] == "E = mc^2"

    assert content[1]["type"] == "blockMath"
    assert content[1]["attrs"]["latex"] == r"\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}"


async def test_import_athmd_ignores_references_section(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    text = (
        "Some prose.\n\n"
        "## References\n\n"
        "1. Someone (2020). Something. *Somewhere*. https://doi.org/10.0/x\n\n"
        '   1.1 "a quote that must not leak into content_json"\n'
    )

    result = await import_athmd(db_session, text, project.id)

    content = result["content_json"]["content"]
    assert len(content) == 1
    assert content[0]["content"][0]["text"] == "Some prose."


async def test_build_then_import_roundtrip(db_session) -> None:
    owner, project = await _seed_project(db_session)
    statement_1 = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="first claim",
    )
    statement_2 = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="second claim",
    )
    db_session.add_all([statement_1, statement_2])
    await db_session.flush()

    original_content = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2},
             "content": [{"type": "text", "text": "Intro"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Plain, then "},
                {"type": "text", "text": "first claim", "marks": [
                    {"type": "bold"},
                    {"type": "statementLink", "attrs": {"statementId": str(statement_1.id)}},
                ]},
                {"type": "text", "text": ", and also "},
                {"type": "text", "text": "second claim", "marks": [
                    {"type": "statementLink", "attrs": {"statementId": str(statement_2.id)}}
                ]},
                {"type": "text", "text": "."},
            ]},
        ],
    }
    doc = ComposeDocument(id=uuid.uuid4(), project_id=project.id, version=1,
                           content_json=original_content)
    db_session.add(doc)
    await db_session.flush()

    exported_text = await build_athmd(db_session, doc.id)
    result = await import_athmd(db_session, exported_text, project.id)

    assert result["warnings"] == []
    assert _flatten(result["content_json"]) == _flatten(original_content)


async def test_build_then_import_math_roundtrip(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    original_content = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Inline math: "},
                {"type": "inlineMath", "attrs": {"latex": "E = mc^2"}},
                {"type": "text", "text": "."},
            ]},
            {"type": "blockMath", "attrs": {"latex": r"\frac{\sqrt{\pi}}{2}"}},
        ],
    }
    doc = ComposeDocument(id=uuid.uuid4(), project_id=project.id, version=1,
                           content_json=original_content)
    db_session.add(doc)
    await db_session.flush()

    exported_text = await build_athmd(db_session, doc.id)
    result = await import_athmd(db_session, exported_text, project.id)

    assert result["warnings"] == []
    content = result["content_json"]["content"]
    inline_math = next(n for n in content[0]["content"] if n["type"] == "inlineMath")
    assert inline_math["attrs"]["latex"] == "E = mc^2"
    assert content[1] == {"type": "blockMath", "attrs": {"latex": r"\frac{\sqrt{\pi}}{2}"}}


async def test_import_compose_document_endpoint_persists_and_reports_warnings(
    db_session,
) -> None:
    """Direct call to the route function (bypassing HTTP/DI, matching this
    suite's existing convention — see test_citations.py's endpoint tests)."""
    owner, project = await _seed_project(db_session)
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="a resolvable claim",
    )
    db_session.add(statement)
    await db_session.flush()

    missing_id = uuid.uuid4()
    text = (
        f"[a resolvable claim](athm:statement/{statement.id}) and "
        f"[an orphaned one](athm:statement/{missing_id}).\n"
    )
    upload = UploadFile(file=io.BytesIO(text.encode()), filename="doc.athmd")

    result = await import_compose_document(project.id, db_session, owner, upload)

    assert result.document.version == 2
    assert len(result.warnings) == 1
    assert result.warnings[0].missing_statement_id == str(missing_id)

    persisted = await db_session.get(ComposeDocument, result.document.id)
    assert persisted is not None
    para = persisted.content_json["content"][0]
    texts = [n["text"] for n in para["content"]]
    assert "a resolvable claim" in texts
    assert "an orphaned one" in texts


async def test_import_athmd_parses_standalone_figure(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    dataset_id = uuid.uuid4()
    text = f"Before.\n\n![Figure 1: a plot](athm:dataset/{dataset_id})\n\nAfter.\n"

    result = await import_athmd(db_session, text, project.id)

    content = result["content_json"]["content"]
    assert content[0]["content"][0]["text"] == "Before."
    assert content[1] == {
        "type": "image",
        "attrs": {"datasetId": str(dataset_id), "alt": "Figure 1: a plot"},
    }
    assert content[2]["content"][0]["text"] == "After."


async def test_build_then_import_figure_roundtrip(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    dataset_id = uuid.uuid4()
    original_content = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Before."}]},
            {"type": "image", "attrs": {"datasetId": str(dataset_id), "alt": "A plot"}},
        ],
    }
    doc = ComposeDocument(id=uuid.uuid4(), project_id=project.id, version=1,
                           content_json=original_content)
    db_session.add(doc)
    await db_session.flush()

    exported_text = await build_athmd(db_session, doc.id)
    result = await import_athmd(db_session, exported_text, project.id)

    assert result["warnings"] == []
    assert result["content_json"]["content"][1] == {
        "type": "image",
        "attrs": {"datasetId": str(dataset_id), "alt": "A plot"},
    }
