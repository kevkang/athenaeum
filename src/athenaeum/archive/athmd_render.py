"""Renders `.athmd` text to HTML (and, from there, to PDF) — the read-only
counterpart to `athmd.py`'s export/import. Works on raw text, not a live
Compose document: by the time `build_athmd()` produces `.athmd` text,
citation numbers and the `## References` section are already baked in as
plain text (see athmd.py's module docstring), so nothing here ever needs
database access — a DB-backed caller is just `build_athmd()` followed by
this module, and an arbitrary hand-written `.athmd` file renders the same
way.

Math (`$..$`/`$$..$$`) is deliberately left as literal delimited text in the
HTML output rather than converted here — it's rendered client-side by KaTeX
wherever the HTML actually ends up displayed (in-app via the frontend's own
katex dependency, or via the vendored copy inlined by
`render_athmd_standalone_html` for a fully offline document). This module
never needs to understand LaTeX itself.

Like the rest of `.athmd`, unsupported content raises loudly rather than
silently dropping it or best-effort guessing.
"""
from __future__ import annotations

import base64
import html
import re
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from athenaeum.archive.athmd import (
    ATHM_DATASET_HREF_RE,
    ATHM_STATEMENT_HREF_RE,
    MARKER_PREFIX_RE,
    REFERENCES_HEADING_RE,
)

DatasetUrlResolver = Callable[[uuid.UUID], "str | None"]

_VENDOR_DIR = Path(__file__).parent / "vendor"
_KATEX_DIR = _VENDOR_DIR / "katex"

_md = MarkdownIt().use(dollarmath_plugin).enable("table")

_MARKER_PAIR_RE = re.compile(r"\[\^(\d+)\.(\d+)\]")

_REF_ENTRY_RE = re.compile(r"^(\d+)\.\s+(.*)$")
_REF_QUOTE_RE = re.compile(r'^\s{3}(\d+\.\d+)\s+"(.*)"\s*$')

_SIMPLE_ITALIC_RE = re.compile(r"\*(.+?)\*")


# ── Public entry points ──────────────────────────────────────────────────────

def render_athmd_body_html(
    text: str, *, dataset_url_resolver: DatasetUrlResolver | None = None
) -> str:
    """The core renderer: `.athmd` text -> an HTML fragment (no `<html>`
    wrapper, no stylesheet, no KaTeX bootstrap) suitable for embedding
    directly into an existing page, e.g. via `dangerouslySetInnerHTML`. Every
    text/attribute value is HTML-escaped at the point of insertion, so the
    output is safe to inject as-is."""
    match = REFERENCES_HEADING_RE.search(text)
    body_text = text[: match.start()] if match else text
    references_text = text[match.start() :] if match else ""

    tokens = _md.parse(body_text)
    body_html = _render_blocks(tokens, dataset_url_resolver)
    references_html = _render_references_html(references_text) if references_text else ""

    return "\n".join(p for p in (body_html, references_html) if p)


def render_athmd_standalone_html(
    text: str, *, title: str = "", dataset_url_resolver: DatasetUrlResolver | None = None
) -> str:
    """Wraps `render_athmd_body_html`'s fragment in a fully self-contained
    HTML document — vendored KaTeX and Pygments CSS/JS are inlined directly
    as string content (fonts as base64 data URIs), so the result has zero
    external requests: safe to email, open from disk, or feed to Playwright
    for a PDF render."""
    body_html = render_athmd_body_html(text, dataset_url_resolver=dataset_url_resolver)
    safe_title = html.escape(title) if title else "Athenaeum document"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>{_katex_css_inline()}</style>
<style>{_pygments_css()}</style>
<style>{_PROSE_CSS}</style>
</head>
<body>
<article class="athmd-prose">
{body_html}
</article>
<script>{_katex_js()}</script>
<script>{_katex_autorender_js()}</script>
<script>
renderMathInElement(document.body, {{
  delimiters: [
    {{left: "$$", right: "$$", display: true}},
    {{left: "$", right: "$", display: false}}
  ],
  throwOnError: false
}});
document.body.dataset.katexRendered = "true";
</script>
</body>
</html>
"""


async def render_athmd_pdf(
    browser: Any,
    text: str,
    *,
    title: str = "",
    dataset_url_resolver: DatasetUrlResolver | None = None,
) -> bytes:
    """Renders `.athmd` text to a PDF via an already-launched Playwright
    `Browser` (see main.py's lifespan — reusing one browser across requests
    avoids a ~1s Chromium cold-start per render)."""
    standalone_html = render_athmd_standalone_html(
        text, title=title, dataset_url_resolver=dataset_url_resolver
    )
    page = await browser.new_page()
    try:
        await page.set_content(standalone_html, wait_until="load")
        await page.wait_for_function("document.body.dataset.katexRendered === 'true'")
        return await page.pdf(format="A4", print_background=True, margin={
            "top": "1in", "bottom": "1in", "left": "1in", "right": "1in",
        })
    finally:
        await page.close()


# ── Block-level rendering ────────────────────────────────────────────────────

def _render_blocks(tokens: list[Token], dataset_url_resolver: DatasetUrlResolver | None) -> str:
    blocks: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            inline_children = tokens[i + 1].children or []
            inner = _render_inline(inline_children)
            blocks.append(f"<{token.tag}>{inner}</{token.tag}>")
            i += 3
        elif token.type == "paragraph_open":
            inline_children = tokens[i + 1].children or []
            image_html = _image_block_html(inline_children, dataset_url_resolver)
            if image_html is not None:
                blocks.append(image_html)
            else:
                inner = _render_inline(inline_children)
                if inner:
                    blocks.append(f"<p>{inner}</p>")
            i += 3
        elif token.type == "math_block":
            latex = token.content.strip()
            blocks.append(f'<div class="athmd-block-math">$${html.escape(latex)}$$</div>')
            i += 1
        elif token.type == "fence":
            language = token.info.strip() if token.info else None
            code_text = token.content.rstrip("\n")
            blocks.append(_render_code_block(code_text, language))
            i += 1
        elif token.type == "table_open":
            table_html, i = _render_table(tokens, i)
            blocks.append(table_html)
        else:
            raise ValueError(f"Unsupported .athmd content for rendering: {token.type!r}")
    return "\n".join(blocks)


def _image_block_html(
    children: list[Token], dataset_url_resolver: DatasetUrlResolver | None
) -> str | None:
    if len(children) != 1 or children[0].type != "image":
        return None
    token = children[0]
    href = (token.attrs or {}).get("src", "")
    match = ATHM_DATASET_HREF_RE.match(str(href))
    if match is None:
        return None
    alt = token.content or ""
    url = dataset_url_resolver(uuid.UUID(match.group(1))) if dataset_url_resolver else None
    if url:
        return (
            f'<figure class="athmd-figure"><img src="{html.escape(url, quote=True)}" '
            f'alt="{html.escape(alt, quote=True)}"></figure>'
        )
    return f'<div class="athmd-figure-placeholder">[figure: {html.escape(alt) or "untitled"}]</div>'


_LEXER_CACHE: dict[str, Any] = {}


def _get_lexer(language: str | None) -> Any:
    key = language or "text"
    lexer = _LEXER_CACHE.get(key)
    if lexer is None:
        try:
            lexer = get_lexer_by_name(key, stripnl=False)
        except ClassNotFound:
            lexer = TextLexer(stripnl=False)
        _LEXER_CACHE[key] = lexer
    return lexer


def _render_code_block(code: str, language: str | None) -> str:
    formatter = HtmlFormatter(nowrap=False, cssclass="athmd-code")
    return highlight(code, _get_lexer(language), formatter)


def _render_table(tokens: list[Token], i: int) -> tuple[str, int]:
    i += 1  # skip table_open
    rows_html: list[str] = []
    while tokens[i].type != "table_close":
        if tokens[i].type in ("thead_open", "thead_close", "tbody_open", "tbody_close"):
            i += 1
            continue
        if tokens[i].type == "tr_open":
            i += 1
            cells_html: list[str] = []
            while tokens[i].type != "tr_close":
                cell_tag = "th" if tokens[i].type == "th_open" else "td"
                inline_children = tokens[i + 1].children or []
                inner = _render_inline(inline_children)
                cells_html.append(f"<{cell_tag}>{inner}</{cell_tag}>")
                i += 3  # th_open/td_open, inline, th_close/td_close
            i += 1  # skip tr_close
            rows_html.append(f"<tr>{''.join(cells_html)}</tr>")
            continue
        raise ValueError(f"Unsupported .athmd table content for rendering: {tokens[i].type!r}")
    i += 1  # skip table_close
    return f"<table>{''.join(rows_html)}</table>", i


# ── Inline-level rendering ───────────────────────────────────────────────────

def _wrap_marks(escaped_text: str, active_marks: list[str]) -> str:
    out = escaped_text
    if "italic" in active_marks:
        out = f"<em>{out}</em>"
    if "bold" in active_marks:
        out = f"<strong>{out}</strong>"
    return out


def _citation_badges(marker_run: str) -> str:
    return "".join(
        f'<sup class="athmd-citation-badge"><a href="#ref-{n}">{n}.{k}</a></sup>'
        for n, k in _MARKER_PAIR_RE.findall(marker_run)
    )


def _render_inline(children: list[Token]) -> str:
    parts: list[str] = []
    active_marks: list[str] = []
    in_statement_link = False
    just_closed_statement_link = False

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
        elif child.type == "code_inline":
            parts.append(f"<code>{html.escape(child.content)}</code>")
            just_closed_statement_link = False
        elif child.type == "link_open":
            href = (child.attrs or {}).get("href", "")
            if ATHM_STATEMENT_HREF_RE.match(str(href)):
                in_statement_link = True
            else:
                parts.append(f'<a href="{html.escape(str(href), quote=True)}">')
        elif child.type == "link_close":
            if in_statement_link:
                in_statement_link = False
                just_closed_statement_link = True
            else:
                parts.append("</a>")
        elif child.type == "text":
            text = child.content
            if just_closed_statement_link:
                marker_match = MARKER_PREFIX_RE.match(text)
                if marker_match:
                    parts.append(_citation_badges(marker_match.group(0)))
                    text = text[marker_match.end() :]
            just_closed_statement_link = False
            if text:
                parts.append(_wrap_marks(html.escape(text), active_marks))
        elif child.type == "math_inline":
            just_closed_statement_link = False
            parts.append(f"${html.escape(child.content)}$")
        elif child.type == "html_inline":
            # A literal `<...>`-shaped span in the source (e.g. "<TODO>")
            # that happens to parse as a raw-HTML token under CommonMark's
            # html rule -- displayed as plain escaped text, not interpreted
            # as markup. This is strictly safer than passing it through
            # (which would risk real HTML/script injection from document
            # text) and more useful than rejecting the whole render over an
            # incidental angle bracket.
            just_closed_statement_link = False
            parts.append(_wrap_marks(html.escape(child.content), active_marks))
        else:
            raise ValueError(f"Unsupported .athmd inline content for rendering: {child.type!r}")

    return "".join(parts)


def _render_simple_inline(text: str) -> str:
    """A tiny fallback for the References section's biblio/quote text, which
    only ever contains plain text plus `*italic*` (see
    athmd.py's `_format_reference_biblio`) — not full inline markdown."""
    escaped = html.escape(text)
    return _SIMPLE_ITALIC_RE.sub(r"<em>\1</em>", escaped)


def _render_references_html(references_text: str) -> str:
    items: list[str] = []
    number: str | None = None
    biblio: str | None = None
    quotes: list[str] = []

    def flush() -> None:
        nonlocal number, biblio, quotes
        if number is not None and biblio is not None:
            quotes_html = f'<ul class="athmd-ref-quotes">{"".join(quotes)}</ul>' if quotes else ""
            items.append(f'<li id="ref-{number}">{_render_simple_inline(biblio)}{quotes_html}</li>')
        number, biblio, quotes = None, None, []

    for line in references_text.splitlines():
        if line.strip() in ("", "## References"):
            continue
        entry_match = _REF_ENTRY_RE.match(line)
        quote_match = _REF_QUOTE_RE.match(line)
        if entry_match:
            flush()
            number, biblio = entry_match.group(1), entry_match.group(2)
        elif quote_match and number is not None:
            sub_id, quote = quote_match.groups()
            quotes.append(f"<li>{sub_id} &ldquo;{_render_simple_inline(quote)}&rdquo;</li>")
    flush()

    if not items:
        return ""
    items_html = "".join(items)
    return f'<section class="athmd-references"><h2>References</h2><ol>{items_html}</ol></section>'


# ── Vendored asset loading ───────────────────────────────────────────────────

_KATEX_FONT_URL_RE = re.compile(
    r'url\(fonts/([\w-]+)\.woff2\)\s*format\("woff2"\),'
    r'url\(fonts/\1\.woff\)\s*format\("woff"\),'
    r'url\(fonts/\1\.ttf\)\s*format\("truetype"\)'
)


@lru_cache(maxsize=1)
def _katex_css_inline() -> str:
    """KaTeX's CSS with its font `url(fonts/...)` references replaced by
    base64-embedded woff2 data URIs (dropping the woff/ttf fallbacks — woff2
    is universally supported by anything that will ever render this: a
    headless-Chromium PDF render, or any browser from the last decade
    opening the standalone file) so the generated HTML needs zero external
    files, not even the font files sitting right next to it."""
    css = (_KATEX_DIR / "katex.min.css").read_text()

    def _inline_font(m: re.Match[str]) -> str:
        font_bytes = (_KATEX_DIR / "fonts" / f"{m.group(1)}.woff2").read_bytes()
        b64 = base64.b64encode(font_bytes).decode("ascii")
        return f'url(data:font/woff2;base64,{b64}) format("woff2")'

    return _KATEX_FONT_URL_RE.sub(_inline_font, css)


@lru_cache(maxsize=1)
def _katex_js() -> str:
    return (_KATEX_DIR / "katex.min.js").read_text()


@lru_cache(maxsize=1)
def _katex_autorender_js() -> str:
    return (_KATEX_DIR / "contrib" / "auto-render.min.js").read_text()


@lru_cache(maxsize=1)
def _pygments_css() -> str:
    return HtmlFormatter(cssclass="athmd-code").get_style_defs(".athmd-code")


@lru_cache(maxsize=1)
def load_principles_athmd() -> str:
    """The proof-of-concept template document served on the public home
    page (`.athmd`'s own README, in `.athmd`) — see PRINCIPLES.md at the
    repo root for the source this was hand-adapted from."""
    return (_VENDOR_DIR / "principles.athmd").read_text()


# Deliberately concrete (not var(--...)) colors -- this is a portable,
# printable document meant to be emailed or opened standalone, not a themed
# panel inside the app's own dark UI.
_PROSE_CSS = """
body { margin: 0; background: #ffffff; }
.athmd-prose {
  max-width: 720px;
  margin: 2.5em auto;
  padding: 0 2em 4em;
  font-family: Georgia, 'Lora', serif;
  font-size: 17px;
  line-height: 1.7;
  color: #1a1a1a;
}
.athmd-prose h1, .athmd-prose h2, .athmd-prose h3, .athmd-prose h4 {
  font-family: Georgia, 'Lora', serif;
  color: #111111;
  line-height: 1.3;
  margin: 1.4em 0 0.5em;
}
.athmd-prose h1:first-child { margin-top: 0; }
.athmd-prose h1 { font-size: 1.8em; }
.athmd-prose h2 { font-size: 1.5em; }
.athmd-prose h3 { font-size: 1.25em; }
.athmd-prose h4 { font-size: 1.1em; }
.athmd-prose p { margin: 0.6em 0; }
.athmd-prose a { color: #2563eb; }
.athmd-prose code {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 0.85em;
  background: #f2f2f2;
  border-radius: 3px;
  padding: 0.1em 0.35em;
}
.athmd-code {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 0.85em;
  line-height: 1.5;
  overflow-x: auto;
  padding: 0.9em 1em;
  border-radius: 6px;
  margin: 1em 0;
}
.athmd-code code { background: none; padding: 0; }
.athmd-prose table { border-collapse: collapse; width: 100%; margin: 1em 0; }
.athmd-prose th, .athmd-prose td {
  border: 1px solid #ddd;
  padding: 0.4em 0.7em;
  text-align: left;
}
.athmd-citation-badge a {
  text-decoration: none;
  color: #2563eb;
  font-size: 0.75em;
}
.athmd-figure-placeholder {
  border: 1px dashed #ccc;
  color: #888;
  padding: 1em;
  text-align: center;
  font-size: 0.9em;
  margin: 1em 0;
}
.athmd-references { margin-top: 2.5em; border-top: 1px solid #ddd; padding-top: 1em; }
.athmd-references h2 { font-size: 1.2em; }
.athmd-references ol { padding-left: 1.4em; }
.athmd-references li { margin: 0.6em 0; }
.athmd-ref-quotes {
  list-style: none;
  padding-left: 1em;
  margin: 0.4em 0 0;
  font-style: italic;
  color: #444;
  font-size: 0.95em;
}
"""
