"""PDF acquisition chain: OA URL → Unpaywall → Semantic Scholar."""
from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx


_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
_PDF_TIMEOUT = httpx.Timeout(10.0, read=60.0)


async def _try_download(url: str, client: httpx.AsyncClient, dest: Path) -> bool:
    try:
        async with client.stream("GET", url, timeout=_PDF_TIMEOUT, follow_redirects=True) as resp:
            if resp.status_code != 200:
                return False
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            if content_type not in _PDF_CONTENT_TYPES:
                return False
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    fh.write(chunk)
        return dest.stat().st_size > 1024  # sanity: at least 1 KB
    except Exception:
        return False


async def _unpaywall_pdf_url(doi: str, email: str, client: httpx.AsyncClient) -> str | None:
    url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}?email={email}"
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        best = data.get("best_oa_location") or {}
        return best.get("url_for_pdf")
    except Exception:
        return None


async def _semantic_scholar_pdf_url(doi: str, client: httpx.AsyncClient) -> str | None:
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf"
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        oa = data.get("openAccessPdf") or {}
        return oa.get("url")
    except Exception:
        return None


async def acquire_pdf(
    doi: str | None,
    oa_url: str | None,
    client: httpx.AsyncClient,
    tmp_dir: Path,
    unpaywall_email: str = "contact@athenaeum.app",
) -> Path | None:
    """Try to download a PDF from available sources. Returns local path or None."""
    dest = tmp_dir / "paper.pdf"

    # 1. OA URL already known from OpenAlex
    if oa_url and await _try_download(oa_url, client, dest):
        return dest

    if not doi:
        return None

    # 2. Unpaywall
    uw_url = await _unpaywall_pdf_url(doi, unpaywall_email, client)
    if uw_url and await _try_download(uw_url, client, dest):
        return dest

    # 3. Semantic Scholar
    ss_url = await _semantic_scholar_pdf_url(doi, client)
    if ss_url and await _try_download(ss_url, client, dest):
        return dest

    return None
