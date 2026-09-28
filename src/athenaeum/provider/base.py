"""Provider protocol — common interface for LLM backends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Message:
    role: str   # "user" | "assistant"
    # A string, or content blocks: an assistant turn resent as-is (e.g. to
    # resume a paused server-tool turn) carries the response's own blocks.
    content: str | list[Any]


class Provider(Protocol):
    """Minimal async LLM interface used by the synthesis orchestrator."""

    async def complete(
        self,
        messages: list[Message],
        system: str,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Return (text_response, tool_use_result).

        Exactly one of the two will be non-None.  When `tools` is provided
        and tool_choice forces a tool call, `tool_use_result` will contain
        {"name": ..., "input": {...}}.
        """
        ...
