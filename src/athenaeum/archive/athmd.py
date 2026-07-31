"""Export a Compose document as a `.athmd` file — a human-readable markdown
manuscript with real nested citations, built on top of the existing TipTap
`content_json` rather than replacing it (see domain/citations.py for the
shared numbering this walks against, and its module docstring for why a
statement can carry more than one citation number).

v1 is export-only; import is a later phase. Any ProseMirror content outside
what Compose's editor currently allows (paragraphs, headings, bold, italic,
statementLink) makes export fail loudly rather than silently dropping it —
the whole point of `.athmd` is that what you see on export is everything
that was actually written.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.citations import (
    CitationNumbering,
    ReferenceEntry,
    build_reference_numbering,
    iter_statement_ids_in_order,
)
from athenaeum.storage.models import ComposeDocument

ATHMD_VERSION = 1

_SUPPORTED_MARKS = {"bold", "italic", "statementLink"}


async def build_athmd(session: AsyncSession, compose_document_id: uuid.UUID) -> str:
    doc = await session.get(ComposeDocument, compose_document_id)
    if doc is None:
        raise ValueError(f"Compose document {compose_document_id} not found")

    statement_ids = iter_statement_ids_in_order(doc.content_json)
    numbering = await build_reference_numbering(session, statement_ids)

    body = _render_blocks(doc.content_json.get("content") or [], numbering)
    references = _render_references(numbering)

    parts = [p for p in (body.rstrip(), references) if p]
    return "\n\n".join(parts) + "\n"


def _render_blocks(nodes: list[dict[str, Any]], numbering: CitationNumbering) -> str:
    blocks: list[str] = []
    for node in nodes:
        node_type = node.get("type")
        if node_type == "paragraph":
            blocks.append(_render_inline(node.get("content") or [], numbering))
        elif node_type == "heading":
            level = (node.get("attrs") or {}).get("level", 1)
            text = _render_inline(node.get("content") or [], numbering)
            blocks.append(f"{'#' * level} {text}")
        else:
            raise ValueError(f"Unsupported .athmd block content: {node_type!r}")
    return "\n\n".join(blocks)


def _render_inline(nodes: list[dict[str, Any]], numbering: CitationNumbering) -> str:
    # Marks are per text-node, not per-span — a single statementLink span
    # can be split across adjacent nodes (e.g. partly bold). Group
    # consecutive nodes sharing the same open statementId into one segment
    # and only close the citation-link wrapper when the id actually changes.
    segments: list[tuple[str | None, list[dict[str, Any]]]] = []
    for node in nodes:
        if node.get("type") != "text":
            raise ValueError(f"Unsupported .athmd inline content: {node.get('type')!r}")
        marks = node.get("marks") or []
        unsupported = {m.get("type") for m in marks} - _SUPPORTED_MARKS
        if unsupported:
            raise ValueError(f"Unsupported .athmd mark(s): {unsupported}")
        statement_id = next(
            (m["attrs"]["statementId"] for m in marks if m.get("type") == "statementLink"), None
        )
        if segments and segments[-1][0] == statement_id:
            segments[-1][1].append(node)
        else:
            segments.append((statement_id, [node]))

    rendered: list[str] = []
    for statement_id, seg_nodes in segments:
        text = "".join(_render_text_node(n) for n in seg_nodes)
        if statement_id is None:
            rendered.append(text)
            continue
        markers = numbering.statement_markers.get(uuid.UUID(statement_id), [])
        suffix = ", ".join(f"[^{m}]" for m in markers)
        rendered.append(f"[{text}](athm:statement/{statement_id})" + suffix)
    return "".join(rendered)


def _render_text_node(node: dict[str, Any]) -> str:
    text = node.get("text", "")
    marks = {m.get("type") for m in (node.get("marks") or [])}
    if "bold" in marks and "italic" in marks:
        return f"***{text}***"
    if "bold" in marks:
        return f"**{text}**"
    if "italic" in marks:
        return f"*{text}*"
    return text


def _format_authors_list(authors_json: Any) -> str:
    """Best-effort 'Lastname FM' formatting for the References section.
    Paper.authors_json only stores OpenAlex/Zotero display_name strings, not
    structured given/family names, so this is a heuristic (last word =
    surname, leading initial from each remaining word) — not exact for
    multi-word surnames or suffixes."""
    names: list[str] = []
    if isinstance(authors_json, list):
        for a in authors_json:
            if isinstance(a, dict):
                name = a.get("name") or a.get("display_name")
            elif isinstance(a, str):
                name = a
            else:
                name = None
            if name:
                names.append(name)
    elif isinstance(authors_json, dict):
        raw_names = authors_json.get("names")
        if isinstance(raw_names, list):
            names = [n for n in raw_names if isinstance(n, str)]

    if not names:
        return "Unknown Author"

    formatted: list[str] = []
    for name in names:
        parts = name.split()
        if len(parts) == 1:
            formatted.append(parts[0])
            continue
        surname = parts[-1]
        initials = "".join(p[0] for p in parts[:-1] if p)
        formatted.append(f"{surname} {initials}")
    return ", ".join(formatted)


def _format_reference_biblio(ref: ReferenceEntry) -> str:
    if ref.paper is not None:
        p = ref.paper
        authors = _format_authors_list(p.authors_json)
        year = f" ({p.year})" if p.year else ""
        title = p.title.rstrip(".")
        venue = f" *{p.venue}*." if p.venue else ""
        doi = f" https://doi.org/{p.doi}" if p.doi else ""
        return f"{authors}{year}. {title}.{venue}{doi}"
    if ref.dataset is not None:
        d = ref.dataset
        return f"**Dataset** ({d.kind}): {d.title}"
    if ref.statement is not None:
        s = ref.statement
        return f'**Statement**: "{s.assertion}"'
    raise ValueError(f"Reference {ref.number} has no resolved metadata")


def _render_references(numbering: CitationNumbering) -> str:
    if not numbering.references:
        return ""
    lines = ["## References", ""]
    for ref in numbering.references:
        lines.append(f"{ref.number}. {_format_reference_biblio(ref)}")
        quote_lines = [e for e in ref.evidence if e.quote is not None]
        if quote_lines:
            lines.append("")
            for e in quote_lines:
                lines.append(f'   {e.sub_id} "{e.quote}"')
        lines.append("")
    return "\n".join(lines).rstrip()
