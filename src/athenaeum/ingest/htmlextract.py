"""Main-article-text extraction for saved HTML (e.g. Zotero web snapshots)."""
from __future__ import annotations

import trafilatura


def extract_main_text(html: str | bytes) -> str | None:
    """Pull boilerplate-free article text out of a saved HTML page.

    Returns text with paragraphs separated by blank lines, matching the
    convention `pipeline.py`'s `_sections_from_text()` already expects for
    plain-text uploads — so this feeds into the exact same ingestion path.
    Returns None if no extractable content is found.
    """
    text = trafilatura.extract(
        html, output_format="txt", include_comments=False, favor_precision=True
    )
    if not text:
        return None
    paragraphs = [line.strip() for line in text.split("\n") if line.strip()]
    if not paragraphs:
        return None
    return "\n\n".join(paragraphs)
