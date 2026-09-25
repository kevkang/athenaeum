"""Independent relevance check for Synthesis passages, run in a fresh context
with no synthesis trace.

It does not judge whether a passage is verbatim. A model can't usefully answer
that about real text, and for passages Synthesis builds it is settled by
construction: the text is copied from stored sentences by id. What remains is a
semantic judgment — does each passage genuinely relate to the claim? — and the
share of passages that do is the synthesis's relevance_rate.
"""
from __future__ import annotations

from typing import Any

import structlog

from athenaeum.provider.base import Message, Provider
from athenaeum.storage.models import Passage

log = structlog.get_logger()


async def verify_passages(
    checker: Provider,
    claim: str,
    passages: list[Passage],
    checker_model_name: str,
    system_prompt: str,
    tool: dict[str, Any],
) -> tuple[float | None, list[bool]]:
    """Ask an independent checker whether each passage is relevant to the claim.

    The checker sees only the claim and each passage's stored text — never the
    extraction step's rationale, retrieval hits, or signal labels — so it can't
    simply agree with the extractor's own reasoning.

    Returns (relevance_rate, per-passage flags). The rate is None when nothing
    was measured: no passages, or a checker that returned no result. A passage
    the checker didn't report on counts as not relevant.
    """
    if not passages:
        return None, []

    # The checker's whole view: the claim plus numbered passage text from the DB.
    passage_lines = "\n\n".join(f"[{i}] {p.verbatim_text}" for i, p in enumerate(passages))
    user_msg = (
        f"CLAIM:\n{claim}\n\n"
        f"PASSAGES ({len(passages)} total):\n{passage_lines}\n\n"
        "For each passage, report whether it genuinely relates to the claim."
    )

    _, tool_result = await checker.complete(
        messages=[Message(role="user", content=user_msg)],
        system=system_prompt,
        max_tokens=2048,
        tools=[tool],
    )

    if tool_result is None:
        log.warning(
            "verifier.no_tool_result", n_passages=len(passages), checker_model=checker_model_name
        )
        return None, [False] * len(passages)

    results: list[dict[str, Any]] = tool_result["input"].get("results", [])

    # Map verdicts back to passages by index; out-of-range indices are ignored
    # rather than fatal, and any passage left unreported defaults to False.
    relevant: dict[int, bool] = {}
    for r in results:
        idx = r.get("passage_index", -1)
        if 0 <= idx < len(passages):
            relevant[idx] = bool(r.get("relevant"))

    flags = [relevant.get(i, False) for i in range(len(passages))]
    rate = sum(flags) / len(flags)

    log.info("verifier.done", relevant=sum(flags), total=len(flags), relevance_rate=rate)
    return rate, flags
