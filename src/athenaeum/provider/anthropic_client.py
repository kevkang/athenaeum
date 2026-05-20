"""Anthropic (Claude) implementation of the Provider protocol."""
from __future__ import annotations

from typing import Any

import anthropic

from athenaeum.provider.base import Message


class AnthropicProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model

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

        for block in response.content:
            if block.type == "tool_use":
                return None, {"name": block.name, "input": block.input}
            if block.type == "text":
                return block.text, None

        return None, None
