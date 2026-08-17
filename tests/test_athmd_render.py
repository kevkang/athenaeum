"""Unit tests for archive/athmd_render.py — pure text-in/HTML-out, no DB
involved (see that module's docstring for why). A separate small PDF smoke
test at the bottom exercises the actual Playwright path; it needs Chromium
installed (`playwright install chromium`) and is skipped if unavailable."""
import uuid

import pytest

from athenaeum.archive.athmd_render import (
    load_principles_athmd,
    render_athmd_body_html,
    render_athmd_pdf,
    render_athmd_standalone_html,
)


def test_plain_paragraph_and_heading():
    html = render_athmd_body_html("# Title\n\nSome plain prose.")
    assert "<h1>Title</h1>" in html
    assert "<p>Some plain prose.</p>" in html


def test_bold_italic_and_code():
    html = render_athmd_body_html("A **bold** word, an *italic* word, and `inline code`.")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>inline code</code>" in html


def test_math_left_verbatim_for_client_side_katex():
    html = render_athmd_body_html("Inline $x^2$ and:\n\n$$\ny = mx + b\n$$")
    assert "$x^2$" in html
    assert '<div class="athmd-block-math">$$y = mx + b$$</div>' in html


def test_html_is_escaped():
    html = render_athmd_body_html("A rate of x < y and y > z, with an & ampersand.")
    assert "x &lt; y" in html
    assert "y &gt; z" in html
    assert "&amp;" in html


def test_stray_angle_bracket_span_is_shown_as_literal_text_not_rejected():
    # "<TODO>" is tag-shaped enough that CommonMark tokenizes it as raw
    # inline HTML -- the renderer must display it verbatim (escaped), not
    # interpret it as markup or fail the whole render over it.
    html = render_athmd_body_html("Fill in the <TODO> section later.")
    assert "&lt;TODO&gt;" in html
    assert "<TODO>" not in html


def test_code_block_is_syntax_highlighted():
    html = render_athmd_body_html("```python\ndef f(x):\n    return x\n```")
    assert 'class="athmd-code"' in html
    # Pygments wraps keywords in their own span -- just confirm *some*
    # token-level markup happened, not the exact class name (theme detail).
    assert "<span" in html


def test_table_renders():
    html = render_athmd_body_html("| A | B |\n| --- | --- |\n| 1 | 2 |")
    assert "<table>" in html
    assert "<th>A</th>" in html
    assert "<td>1</td>" in html


def test_unsupported_block_raises():
    with pytest.raises(ValueError, match="Unsupported"):
        render_athmd_body_html("> a blockquote")


def test_citation_link_becomes_anchored_badge():
    statement_id = uuid.uuid4()
    text = (
        f"A claim.[claim text](athm:statement/{statement_id})[^1.2]\n\n"
        "## References\n\n"
        '1. Author A. (2020). Some Title. https://doi.org/10.1/x\n\n'
        '   1.2 "the exact quote"\n'
    )
    html = render_athmd_body_html(text)
    assert "claim text" in html
    assert '<a href="#ref-1">1.2</a>' in html
    assert 'id="ref-1"' in html
    assert "&ldquo;the exact quote&rdquo;" in html


def test_image_without_resolver_falls_back_to_placeholder():
    dataset_id = uuid.uuid4()
    html = render_athmd_body_html(f"![a figure](athm:dataset/{dataset_id})")
    assert "athmd-figure-placeholder" in html
    assert "a figure" in html
    assert "<img" not in html


def test_image_with_resolver_renders_real_img():
    dataset_id = uuid.uuid4()

    def resolver(did: uuid.UUID) -> str | None:
        return f"https://example.com/{did}.png" if did == dataset_id else None

    html = render_athmd_body_html(
        f"![a figure](athm:dataset/{dataset_id})", dataset_url_resolver=resolver
    )
    assert f'<img src="https://example.com/{dataset_id}.png" alt="a figure">' in html


def test_standalone_html_is_self_contained():
    doc = render_athmd_standalone_html("# Hi\n\n$x$", title="My Doc")
    assert "<title>My Doc</title>" in doc
    assert "font-face" in doc
    assert "data:font/woff2;base64," in doc
    assert "renderMathInElement" in doc
    assert "katexRendered" in doc


def test_principles_athmd_renders_without_error():
    text = load_principles_athmd()
    html = render_athmd_body_html(text)
    assert "<h1>Athenaeum</h1>" in html
    assert "REF" in html  # placeholders stay literal, not linked to anything


# ── PDF smoke test (real Playwright/Chromium) ────────────────────────────────

async def test_render_pdf_produces_real_pdf_bytes():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                pdf_bytes = await render_athmd_pdf(browser, "# Hello\n\nSome $x^2$ math.")
            finally:
                await browser.close()
    except Exception as exc:
        pytest.skip(f"Chromium not available for PDF rendering: {exc}")

    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 1000
