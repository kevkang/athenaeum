"""Submit a PDF to GROBID and parse the returned TEI XML into sections."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx


_TEI_NS = "http://www.tei-c.org/ns/1.0"
_NS = {"tei": _TEI_NS}
_GROBID_TIMEOUT = httpx.Timeout(30.0, read=300.0)


@dataclass
class Section:
    label: str
    text: str


def _el_text(element: ET.Element) -> str:
    """Recursively collect all text content from an element."""
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        parts.append(_el_text(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(p.strip() for p in parts if p.strip())


def _parse_tei(tei_xml: str) -> list[Section]:
    root = ET.fromstring(tei_xml)
    body = root.find(f".//{{{_TEI_NS}}}body")
    if body is None:
        return []

    sections: list[Section] = []
    for i, div in enumerate(body.findall(f"{{{_TEI_NS}}}div")):
        head = div.find(f"{{{_TEI_NS}}}head")
        label = head.text.strip() if head is not None and head.text else f"Section {i + 1}"

        paragraphs: list[str] = []
        for p in div.findall(f".//{{{_TEI_NS}}}p"):
            text = _el_text(p).strip()
            if text:
                paragraphs.append(text)

        if not paragraphs:
            # Some sections have text directly in divs without <p>
            text = _el_text(div).strip()
            # Strip the heading text if it was already captured
            if head is not None and head.text and text.startswith(head.text):
                text = text[len(head.text):].strip()
            if text:
                paragraphs.append(text)

        full_text = "\n".join(paragraphs)
        if full_text:
            sections.append(Section(label=label, text=full_text))

    return sections


async def parse_pdf(pdf_path: Path, grobid_url: str, client: httpx.AsyncClient) -> list[Section]:
    """Send PDF to GROBID, parse TEI XML, return sections."""
    endpoint = f"{grobid_url.rstrip('/')}/api/processFulltextDocument"

    with pdf_path.open("rb") as fh:
        resp = await client.post(
            endpoint,
            files={"input": ("paper.pdf", fh, "application/pdf")},
            data={"consolidateHeader": "1", "consolidateCitations": "0"},
            timeout=_GROBID_TIMEOUT,
        )

    resp.raise_for_status()
    return _parse_tei(resp.text)
