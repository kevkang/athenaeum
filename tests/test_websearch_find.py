from types import SimpleNamespace

from athenaeum.synthesize.websearch_find import (
    _web_search_tool_for_model,
    find_via_web_search,
)


def _citation(
    url: str, title: str | None, cited_text: str, kind: str = "web_search_result_location"
):
    return SimpleNamespace(type=kind, url=url, title=title, cited_text=cited_text)


def _text_block(text: str, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


class _FakeProvider:
    def __init__(self, content: list, model: str = "claude-haiku-4-5-20251001") -> None:
        self._response = SimpleNamespace(content=content)
        self.model = model
        self.last_call: dict | None = None

    async def complete_raw(self, messages, system, max_tokens=4096, tools=None):
        self.last_call = {
            "messages": messages, "system": system, "tools": tools, "max_tokens": max_tokens,
        }
        return self._response


def test_web_search_tool_uses_dynamic_filtering_for_current_models() -> None:
    tool = _web_search_tool_for_model("claude-opus-4-8", max_uses=5)
    assert tool["type"] == "web_search_20260209"
    assert tool["name"] == "web_search"
    assert tool["max_uses"] == 5


def test_web_search_tool_uses_basic_variant_for_older_models() -> None:
    tool = _web_search_tool_for_model("claude-haiku-4-5-20251001", max_uses=5)
    assert tool["type"] == "web_search_20250305"


async def test_find_via_web_search_extracts_citations_as_matches() -> None:
    content = [
        _text_block(
            "Methane fluxes rise sharply with soil temperature, per one field study.",
            citations=[_citation(
                "https://example.com/paper", "Permafrost Methane Study",
                "Methane fluxes rise sharply with temperature.",
            )],
        ),
    ]
    provider = _FakeProvider(content)

    matches = await find_via_web_search(provider, "methane permafrost")

    assert len(matches) == 1
    assert matches[0].url == "https://example.com/paper"
    assert matches[0].title == "Permafrost Methane Study"
    assert matches[0].quote == "Methane fluxes rise sharply with temperature."
    assert "Methane fluxes rise sharply" in matches[0].relevance_note

    # tool_choice must NOT be forced — web search is autonomous, not a forced tool call.
    assert provider.last_call["tools"][0]["name"] == "web_search"


async def test_find_via_web_search_dedupes_by_url() -> None:
    content = [
        _text_block("First mention.", citations=[_citation("https://a.com", "A", "quote one")]),
        _text_block(
            "Second mention of the same source.",
            citations=[_citation("https://a.com", "A", "quote two")],
        ),
    ]
    provider = _FakeProvider(content)

    matches = await find_via_web_search(provider, "query")

    assert len(matches) == 1
    assert matches[0].quote == "quote one"


async def test_find_via_web_search_ignores_non_text_blocks_and_non_web_citations() -> None:
    content = [
        SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "x"}),
        SimpleNamespace(type="web_search_tool_result", content=[]),
        _text_block(
            "A claim citing a document, not the web.",
            citations=[_citation("doc://x", "Doc", "quote", kind="page_location")],
        ),
        _text_block("No citations here at all.", citations=None),
    ]
    provider = _FakeProvider(content)

    matches = await find_via_web_search(provider, "query")

    assert matches == []


async def test_find_via_web_search_falls_back_to_url_when_no_title() -> None:
    content = [
        _text_block(
            "A source with no title.", citations=[_citation("https://b.com", None, "quote")]
        )
    ]
    provider = _FakeProvider(content)

    matches = await find_via_web_search(provider, "query")

    assert matches[0].title == "https://b.com"
