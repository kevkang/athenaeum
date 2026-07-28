"""Tests for the independent citation-verification pass.

verify_passages() is the single most credibility-critical function in the
codebase: it produces the "verified" flag and verification_rate that the
product's core claim (a measured, not claimed, citation-verification rate)
rests on. These tests exist to protect two things: the verdict logic itself,
and — the more important property — that the checker's context is actually
isolated from the extraction step's reasoning, not just isolated by
convention.
"""
import uuid

from athenaeum.provider.base import Message
from athenaeum.storage.models import Passage
from athenaeum.synthesize.verifier import verify_passages

_TOOL: dict = {"name": "verify_passages", "input_schema": {}}
_SYSTEM = "You are an independent fact checker."


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None
        self.last_system: str | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        self.last_system = system
        return None, self._tool_result


def _passage(verbatim_text: str, rationale: str | None = None) -> Passage:
    return Passage(
        id=uuid.uuid4(),
        paper_id=uuid.uuid4(),
        sentence_ids=[uuid.uuid4()],
        verbatim_text=verbatim_text,
        rationale=rationale,
    )


async def test_empty_passages_short_circuits_without_calling_the_provider() -> None:
    provider = _FakeProvider(None)

    rate, flags = await verify_passages(provider, "claim", [], "model", _SYSTEM, _TOOL)

    assert (rate, flags) == (0.0, [])
    assert provider.last_messages is None


async def test_no_tool_result_marks_everything_unverified() -> None:
    provider = _FakeProvider(None)
    passages = [_passage("Sentence one."), _passage("Sentence two.")]

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert rate == 0.0
    assert flags == [False, False]


async def test_verified_requires_both_verbatim_plausible_and_relevant() -> None:
    passages = [_passage("A"), _passage("B"), _passage("C"), _passage("D")]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [
            {"passage_index": 0, "verbatim_plausible": True, "relevant": True},
            {"passage_index": 1, "verbatim_plausible": True, "relevant": False},
            {"passage_index": 2, "verbatim_plausible": False, "relevant": True},
            {"passage_index": 3, "verbatim_plausible": False, "relevant": False},
        ]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert flags == [True, False, False, False]
    assert rate == 0.25


async def test_missing_passage_index_defaults_to_unverified() -> None:
    passages = [_passage("A"), _passage("B")]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [{"passage_index": 0, "verbatim_plausible": True, "relevant": True}]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    # Passage 1 was never reported on — must default to unverified, not be
    # skipped or crash the aggregate rate calculation.
    assert flags == [True, False]
    assert rate == 0.5


async def test_out_of_range_passage_index_is_ignored_not_fatal() -> None:
    passages = [_passage("A")]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [
            {"passage_index": 0, "verbatim_plausible": True, "relevant": True},
            {"passage_index": 7, "verbatim_plausible": True, "relevant": True},
            {"passage_index": -1, "verbatim_plausible": True, "relevant": True},
        ]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert flags == [True]
    assert rate == 1.0


async def test_checker_context_excludes_extraction_rationale() -> None:
    """The independent-checker design only means something if the checker's
    prompt genuinely can't see the extraction step's own reasoning for why
    it picked a passage — otherwise it can rubber-stamp instead of actually
    checking. rationale is set on the Passage (as the extractor would set
    it) but must never appear in what the checker is sent."""
    passages = [_passage(
        "Melatonin reduces oxidative stress in aged mitochondria.",
        rationale="I selected this because it directly states the causal claim the user asked.",
    )]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [{"passage_index": 0, "verbatim_plausible": True, "relevant": True}]},
    })

    await verify_passages(
        provider, "melatonin reduces oxidative stress", passages, "model", _SYSTEM, _TOOL,
    )

    assert provider.last_messages is not None
    sent_text = "\n".join(m.content for m in provider.last_messages)
    assert "directly states the causal claim" not in sent_text
    assert "Melatonin reduces oxidative stress in aged mitochondria." in sent_text
    # The checker also shouldn't be told which system prompt/persona the
    # extraction step used — only its own.
    assert provider.last_system == _SYSTEM


async def test_verification_rate_is_the_verified_fraction() -> None:
    passages = [_passage(f"Sentence {i}.") for i in range(4)]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [
            {"passage_index": 0, "verbatim_plausible": True, "relevant": True},
            {"passage_index": 1, "verbatim_plausible": True, "relevant": True},
            {"passage_index": 2, "verbatim_plausible": True, "relevant": True},
            {"passage_index": 3, "verbatim_plausible": False, "relevant": True},
        ]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert flags.count(True) == 3
    assert rate == 0.75
