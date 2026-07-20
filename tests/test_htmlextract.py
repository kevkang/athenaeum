from pathlib import Path

from athenaeum.ingest.htmlextract import extract_main_text

FIXTURE = Path(__file__).parent / "fixtures" / "zotero_snapshot_sample.html"


def test_extracts_article_paragraphs_not_boilerplate() -> None:
    html = FIXTURE.read_text()
    text = extract_main_text(html)
    assert text is not None
    assert "Migration Patterns of Example Birds" in text
    assert "two hundred individuals" in text
    # boilerplate should be stripped
    assert "Subscribe" not in text
    assert "Privacy Policy" not in text


def test_paragraphs_are_blank_line_delimited() -> None:
    html = FIXTURE.read_text()
    text = extract_main_text(html)
    assert text is not None
    blocks = [b for b in text.split("\n\n") if b.strip()]
    assert len(blocks) >= 3


def test_empty_html_returns_none() -> None:
    assert extract_main_text("<html><body></body></html>") is None
