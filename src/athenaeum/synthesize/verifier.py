"""Independent checker pass — runs in a fresh context with no synthesis trace."""
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
) -> tuple[float, list[bool]]:
    """Run the independent verification pass.

    The checker receives ONLY the claim text and the verbatim passage text from DB.
    It has no access to the synthesis trace, retrieval hits, or signal classifications.

    Returns (verification_rate, per_passage_bool_list).
    """
    if not passages:
        return 0.0, []

    # Build the checker's view: claim + numbered passages (text from DB only)
    passage_lines = "\n\n".join(
        f"[{i}] {p.verbatim_text}"
        for i, p in enumerate(passages)
    )
    user_msg = (
        f"CLAIM TO VERIFY AGAINST:\n{claim}\n\n"
        f"PASSAGES ({len(passages)} total):\n{passage_lines}\n\n"
        "For each passage, report whether the text is plausibly verbatim scientific "
        "prose and whether it is relevant to the claim."
    )

    _, tool_result = await checker.complete(
        messages=[Message(role="user", content=user_msg)],
        system=system_prompt,
        max_tokens=2048,
        tools=[tool],
    )

    if tool_result is None:
        log.warning("verifier.no_tool_result", n_passages=len(passages))
        return 0.0, [False] * len(passages)

    results: list[dict[str, Any]] = tool_result["input"].get("results", [])

    # Map back by passage_index
    verdict: dict[int, bool] = {}
    for r in results:
        idx = r.get("passage_index", -1)
        if 0 <= idx < len(passages):
            # A passage is "verified" if the checker believes it is both
            # verbatim plausible AND relevant to the claim.
            verdict[idx] = bool(r.get("verbatim_plausible")) and bool(r.get("relevant"))

    verified_flags = [verdict.get(i, False) for i in range(len(passages))]
    rate = sum(verified_flags) / len(verified_flags) if verified_flags else 0.0

    log.info(
        "verifier.done",
        verified=sum(verified_flags),
        total=len(verified_flags),
        rate=rate,
    )
    return rate, verified_flags
