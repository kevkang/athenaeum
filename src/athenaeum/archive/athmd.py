"""Export/import a Compose document as a `.athmd` file — a human-readable
markdown manuscript with real nested citations, built on top of the
existing TipTap `content_json` rather than replacing it (see
domain/citations.py for the shared numbering export walks against, and its
module docstring for why a statement can carry more than one citation
number).

Any ProseMirror content outside what Compose's editor currently allows
(paragraphs, headings, bold, italic, statementLink, inline/block math)
makes export fail loudly rather than silently dropping it — the whole
point of `.athmd` is that what you see on export is everything that was
actually written.

Import is scoped to same-project reconciliation, not the `.athtrace`-style
portable cross-project share: every `athm:statement/<uuid>` found in the
text is checked against the importing project's own statements. A resolved
id recreates the mark; an unresolved one (foreign project, deleted
statement) degrades to plain text and is reported back as a warning rather
than failing the whole import. The `## References` section is never parsed
back — reference numbers and evidence sub-ids are always a cached
rendering, recomputed fresh from `StatementEvidence` on the next export or
render (see the round-trip fidelity note in the design discussion this
module was built from), never trusted as source of truth.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.citations import (
    CitationNumbering,
    ReferenceEntry,
    build_reference_numbering,
    iter_statement_ids_in_order,
)
from athenaeum.storage.models import ComposeDocument, Statement

ATHMD_VERSION = 1

_SUPPORTED_MARKS = {"bold", "italic", "statementLink"}
_SUPPORTED_INLINE_TYPES = {"text", "inlineMath"}

_ATHM_STATEMENT_HREF_RE = re.compile(r"^athm:statement/([0-9a-fA-F-]{36})$")
_ATHM_DATASET_HREF_RE = re.compile(r"^athm:dataset/([0-9a-fA-F-]{36})$")
# The literal `[^N.k]` citation markers export appends right after a
# statement link (comma-space joined) aren't real footnote syntax — nothing
# defines them — so on import they show up as ordinary leading text right
# after the link closes. This strips that leading run so it isn't
# reimported as visible prose; anything left over (real text that followed
# the citations in the original) is untouched. The `, ` between markers is
# only ever consumed when it's immediately followed by another marker —
# export never emits a trailing separator after the last one, so a comma
# right after it belongs to whatever prose came next (e.g. ", though ...").
_MARKER_PREFIX_RE = re.compile(r"^\[\^\d+\.\d+\](?:, \[\^\d+\.\d+\])*")

_REFERENCES_HEADING_RE = re.compile(r"^## References\s*$", re.MULTILINE)

_md = MarkdownIt().use(dollarmath_plugin)


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
        elif node_type == "blockMath":
            latex = (node.get("attrs") or {}).get("latex", "")
            blocks.append(f"$$\n{latex}\n$$")
        elif node_type == "image":
            attrs = node.get("attrs") or {}
            dataset_id = attrs.get("datasetId")
            alt = attrs.get("alt") or ""
            blocks.append(f"![{alt}](athm:dataset/{dataset_id})")
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
        node_type = node.get("type")
        if node_type not in _SUPPORTED_INLINE_TYPES:
            raise ValueError(f"Unsupported .athmd inline content: {node_type!r}")
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
        text = "".join(_render_inline_node(n) for n in seg_nodes)
        if statement_id is None:
            rendered.append(text)
            continue
        markers = numbering.statement_markers.get(uuid.UUID(statement_id), [])
        suffix = ", ".join(f"[^{m}]" for m in markers)
        rendered.append(f"[{text}](athm:statement/{statement_id})" + suffix)
    return "".join(rendered)


def _render_inline_node(node: dict[str, Any]) -> str:
    if node.get("type") == "inlineMath":
        latex = (node.get("attrs") or {}).get("latex", "")
        return f"${latex}$"
    return _render_text_node(node)


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


async def import_athmd(
    session: AsyncSession, text: str, project_id: uuid.UUID
) -> dict[str, Any]:
    """Parse `.athmd` prose back into ProseMirror content_json. Doesn't
    touch the DB itself — the caller decides what to do with the result
    (the API route PUTs it through the existing `save_compose_document`
    path). `warnings` is one entry per statement link that didn't resolve
    against this project, `{"span_text": ..., "missing_statement_id": ...}`."""
    body_text = _strip_references_section(text)
    tokens = _md.parse(body_text)
    known_ids = await _known_statement_ids(session, project_id)

    warnings: list[dict[str, str]] = []
    content: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline_children = tokens[i + 1].children or []
            content.append({
                "type": "heading",
                "attrs": {"level": level},
                "content": _import_inline_tokens(inline_children, known_ids, warnings),
            })
            i += 3  # heading_open, inline, heading_close
        elif token.type == "paragraph_open":
            inline_children = tokens[i + 1].children or []
            image_node = _image_block_from_paragraph(inline_children)
            if image_node is not None:
                # Tiptap's Image node is block-level (a sibling of
                # paragraph/heading, never nested inside one) — but plain
                # CommonMark only has an *inline* image token, always
                # wrapped in a paragraph. A paragraph containing nothing
                # but one image is what export produces for a figure, so
                # unwrap it back to a bare block on import.
                content.append(image_node)
            else:
                rendered = _import_inline_tokens(inline_children, known_ids, warnings)
                content.append({"type": "paragraph", "content": rendered} if rendered
                                else {"type": "paragraph"})
            i += 3  # paragraph_open, inline, paragraph_close
        elif token.type == "math_block":
            content.append({"type": "blockMath", "attrs": {"latex": token.content.strip()}})
            i += 1
        else:
            raise ValueError(f"Unsupported .athmd content on import: {token.type!r}")

    if not content:
        content = [{"type": "paragraph"}]

    return {"content_json": {"type": "doc", "content": content}, "warnings": warnings}


def _strip_references_section(text: str) -> str:
    match = _REFERENCES_HEADING_RE.search(text)
    return text if match is None else text[: match.start()]


def _image_block_from_paragraph(children: list[Token]) -> dict[str, Any] | None:
    """If a paragraph's inline content is nothing but a single
    `![alt](athm:dataset/<uuid>)`, return the top-level `image` block it
    round-trips to; otherwise None (an ordinary paragraph, or an
    `athm:dataset` link mixed with other content, which `_import_inline_tokens`
    will reject — figures are standalone blocks in v1, matching the
    always-block-level `image` node Compose's editor actually produces)."""
    if len(children) != 1 or children[0].type != "image":
        return None
    token = children[0]
    href = (token.attrs or {}).get("src", "")
    match = _ATHM_DATASET_HREF_RE.match(str(href))
    if match is None:
        return None
    return {"type": "image", "attrs": {"datasetId": match.group(1), "alt": token.content or ""}}


async def _known_statement_ids(session: AsyncSession, project_id: uuid.UUID) -> set[uuid.UUID]:
    ids = await session.scalars(select(Statement.id).where(Statement.project_id == project_id))
    return set(ids)


def _import_inline_tokens(
    children: list[Token], known_ids: set[uuid.UUID], warnings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    active_marks: list[str] = []
    current_statement_id: str | None = None
    just_closed_link = False

    for child in children:
        if child.type == "strong_open":
            active_marks.append("bold")
        elif child.type == "strong_close":
            if "bold" in active_marks:
                active_marks.remove("bold")
        elif child.type == "em_open":
            active_marks.append("italic")
        elif child.type == "em_close":
            if "italic" in active_marks:
                active_marks.remove("italic")
        elif child.type == "link_open":
            href = (child.attrs or {}).get("href", "")
            match = _ATHM_STATEMENT_HREF_RE.match(str(href))
            if match is None:
                raise ValueError(f"Unsupported .athmd link target on import: {href!r}")
            current_statement_id = match.group(1)
        elif child.type == "link_close":
            current_statement_id = None
            just_closed_link = True
        elif child.type == "text":
            text = child.content
            if just_closed_link:
                text = _MARKER_PREFIX_RE.sub("", text, count=1)
            just_closed_link = False
            if not text:
                continue
            marks = _resolve_span_marks(
                active_marks, current_statement_id, text, known_ids, warnings
            )
            node: dict[str, Any] = {"type": "text", "text": text}
            if marks:
                node["marks"] = marks
            nodes.append(node)
        elif child.type == "math_inline":
            just_closed_link = False
            marks = _resolve_span_marks(
                active_marks, current_statement_id, child.content, known_ids, warnings
            )
            node = {"type": "inlineMath", "attrs": {"latex": child.content}}
            if marks:
                node["marks"] = marks
            nodes.append(node)
        else:
            raise ValueError(f"Unsupported .athmd inline content on import: {child.type!r}")

    return nodes


def _resolve_span_marks(
    active_marks: list[str],
    current_statement_id: str | None,
    span_text: str,
    known_ids: set[uuid.UUID],
    warnings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = [{"type": m} for m in active_marks]
    if current_statement_id is not None:
        if uuid.UUID(current_statement_id) in known_ids:
            marks.append({"type": "statementLink", "attrs": {"statementId": current_statement_id}})
        else:
            warnings.append({"span_text": span_text, "missing_statement_id": current_statement_id})
    return marks
