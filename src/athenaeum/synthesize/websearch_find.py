""""Find" via Claude's own web search — the model autonomously searches the
web (Anthropic's server-side web_search tool) and cites its sources in its
response text. Architecturally distinct from the other Find corpora (Zotero,
OpenAlex): there is no candidate list to rank, since the model does its own
retrieval server-side in one request. Results are arbitrary web pages — no
DOI, nothing bibliographic to import — so the "quote" and "relevance note"
come directly from the model's own cited response text via the SDK's
citation objects, not from a separate ranking pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from athenaeum.provider.anthropic_client import AnthropicProvider
from athenaeum.provider.base import Message

log = structlog.get_logger()

# Room for the whole turn: with the dynamic-filtering variant, the code the
# model writes to filter results is billed as output too, and it must still
# write one cited paragraph per source after up to max_uses searches. A
# response cut off by max_tokens before that writing has no citations, and
# so reads as "nothing found".
_MAX_TOKENS = 16000
# The server pauses a long search turn (stop_reason "pause_turn"); sending the
# paused turn back resumes it. Bounded so a turn can't loop indefinitely.
_MAX_CONTINUATIONS = 3

# Current-generation models support the newer web_search variant with
# built-in dynamic filtering; anything else falls back to the basic variant.
_DYNAMIC_FILTERING_MODELS = {
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
}


def _web_search_tool_for_model(model: str, max_uses: int) -> dict:
    kind = "web_search_20260209" if model in _DYNAMIC_FILTERING_MODELS else "web_search_20250305"
    return {"type": kind, "name": "web_search", "max_uses": max_uses}


WEB_SEARCH_FIND_SYSTEM = """\
You are Find, a research search assistant inside Athenaeum, a research-traceability tool.
The user has given you a search query. Use web search to find sources genuinely relevant to
it — search as many times as needed to cover the topic from different angles.

Your job is NOT to write a summary or a synthesized essay. For each relevant source, write one
short, self-contained paragraph that names the source and includes a direct quote from it.
One source and one quote per paragraph — do not combine multiple sources into one paragraph.
Add at most one line on why the source is relevant. If nothing relevant turns up, say so
plainly rather than stretching a weak result to fit.
"""


@dataclass
class WebSearchMatch:
    url: str
    title: str
    quote: str
    relevance_note: str


async def find_via_web_search(
    provider: AnthropicProvider, query: str, max_uses: int = 5
) -> list[WebSearchMatch]:
    """Runs one request with the web_search tool attached (tool_choice left as
    the default "auto" — the model decides whether/how many times to search),
    then reads verbatim quotes straight off each text block's own citations
    rather than parsing the raw search-result blocks (which carry only a
    title/url/opaque encrypted_content, no readable snippet)."""
    tool = _web_search_tool_for_model(provider.model, max_uses)
    content, stop_reason, continuations = await _run_search_turn(
        provider, WEB_SEARCH_FIND_SYSTEM, query, tool)

    matches: list[WebSearchMatch] = []
    seen_urls: set[str] = set()
    for block in content:
        if block.type != "text" or not block.citations:
            continue
        for citation in block.citations:
            if citation.type != "web_search_result_location" or citation.url in seen_urls:
                continue
            seen_urls.add(citation.url)
            matches.append(
                WebSearchMatch(
                    url=citation.url,
                    title=citation.title or citation.url,
                    quote=citation.cited_text,
                    relevance_note=block.text.strip(),
                )
            )
    # Why a search came back as it did: a truncated or still-paused turn and a
    # genuinely empty web look identical from the matches alone.
    log.info(
        "web_search_find.done",
        stop_reason=stop_reason,
        continuations=continuations,
        searches=sum(1 for b in content if b.type == "server_tool_use"),
        text_blocks=sum(1 for b in content if b.type == "text"),
        matches=len(matches),
    )
    if stop_reason in ("max_tokens", "pause_turn"):
        log.warning("web_search_find.incomplete", stop_reason=stop_reason, matches=len(matches))
    return matches


async def _run_search_turn(
    provider: AnthropicProvider, system: str, query: str, tool: dict
) -> tuple[list[Any], str | None, int]:
    """One web-search turn, resumed while the server pauses it. Returns every
    content block across the turn, the final stop reason, and how many times
    it was resumed."""
    messages = [Message(role="user", content=query)]
    response = await provider.complete_raw(
        messages=messages, system=system, tools=[tool], max_tokens=_MAX_TOKENS,
    )
    content: list[Any] = list(response.content)
    continuations = 0
    while response.stop_reason == "pause_turn" and continuations < _MAX_CONTINUATIONS:
        # Resend the paused turn as-is, with no extra "continue" message: the
        # trailing server_tool_use block tells the API to resume the turn.
        continuations += 1
        response = await provider.complete_raw(
            messages=[*messages, Message(role="assistant", content=list(content))],
            system=system, tools=[tool], max_tokens=_MAX_TOKENS,
        )
        content.extend(response.content)
    return content, response.stop_reason, continuations


# ── Pages for the Nucleate archivist ─────────────────────────────────────────

WEB_SEARCH_PAGES_SYSTEM = """\
You are searching the web for scholarly literature: journal articles, preprints, theses and
conference papers, on the publisher's, repository's or preprint server's own pages where
possible. Search from a few different angles. Do not summarize or answer anything: the search
results themselves are all that is used.
"""


@dataclass
class WebPage:
    url: str
    title: str


async def search_web_pages(
    provider: AnthropicProvider, query: str, max_uses: int = 3
) -> list[WebPage]:
    """The pages a web search turned up, read straight from its result blocks.

    Always the basic web_search variant. The dynamic-filtering variant passes
    results through code the model writes, so its answer cites nothing and
    its result blocks may never reach the response: a finder that reads
    either one gets nothing back while paying for every search. The
    archivist only needs each page's URL and title, which it matches to a
    scholarly work deterministically, so no written answer is needed.
    """
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}
    content, stop_reason, continuations = await _run_search_turn(
        provider, WEB_SEARCH_PAGES_SYSTEM, query, tool)

    pages: list[WebPage] = []
    seen: set[str] = set()
    errors: list[str] = []
    for block in content:
        if block.type != "web_search_tool_result":
            continue
        # A failed search's content is one error object, not a list.
        if not isinstance(block.content, list):
            errors.append(getattr(block.content, "error_code", "unknown"))
            continue
        for result in block.content:
            if getattr(result, "type", None) != "web_search_result" or result.url in seen:
                continue
            seen.add(result.url)
            pages.append(WebPage(url=result.url, title=result.title or result.url))
    log.info(
        "web_search_pages.done",
        stop_reason=stop_reason,
        continuations=continuations,
        searches=sum(1 for b in content if b.type == "server_tool_use"),
        pages=len(pages),
        errors=errors or None,
    )
    return pages
