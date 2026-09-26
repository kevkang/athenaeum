"""Anthropic (Claude) implementation of the Provider protocol."""
from __future__ import annotations

from typing import Any

import anthropic
from anthropic.types import Message as AnthropicMessage

from athenaeum.provider.base import Message


class AnthropicProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        # Running token totals across every call made through this provider, so
        # a batch job (an evaluation run, say) can report what it actually used.
        self.input_tokens = 0
        self.output_tokens = 0

    async def complete(
        self,
        messages: list[Message],
        system: str,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": "any"}

        response = await self._client.messages.create(**kwargs)
        self._record_usage(response)

        for block in response.content:
            if block.type == "tool_use":
                return None, {"name": block.name, "input": block.input}
            if block.type == "text":
                return block.text, None

        return None, None

    def _record_usage(self, response: AnthropicMessage) -> None:
        """Add one response's billed tokens to the running totals. Output tokens
        include any thinking the model did, which is billed as output."""
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens

    async def complete_raw(
        self,
        messages: list[Message],
        system: str,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> AnthropicMessage:
        """Like complete(), but returns the full raw SDK response untouched.

        complete()'s collapsed (text | tool_use) return can only carry a
        single block — callers that need every interleaved content block
        (e.g. web search: text blocks with citations, plus the raw
        web_search_tool_result blocks) need the real response object. Unlike
        complete(), this never forces tool_choice — server-side tools like
        web search should be invoked autonomously (tool_choice: auto, the
        default), not forced on the first turn.
        """
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        if tools:
            kwargs["tools"] = tools
        response = await self._client.messages.create(**kwargs)
        self._record_usage(response)
        return response


async def validate_api_key(api_key: str) -> None:
    """Check that an Anthropic API key is valid without spending inference tokens.

    `models.list()` is a free metadata call that still requires a valid,
    authenticated key — raises anthropic.AuthenticationError/PermissionDeniedError
    on a bad key, anthropic.RateLimitError if the key is being rate-limited.
    """
    client = anthropic.AsyncAnthropic(api_key=api_key)
    await client.models.list(limit=1)
