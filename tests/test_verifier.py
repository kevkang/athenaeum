"""Tests for the independent relevance check on Synthesis passages.

verify_passages() produces each passage's `verified` flag and the synthesis's
relevance_rate, which is a published number. These tests protect three things:
that relevance alone decides the verdict (whether a quote is verbatim follows
from how passages are built, and is not something a model is asked to judge),
that an unmeasured rate is None rather than 0, and that the checker's context
is actually isolated from the extraction step's reasoning.
"""
import uuid

import pytest

from athenaeum.provider.base import Message
from athenaeum.storage.models import Passage
from athenaeum.synthesize.prompts import VERIFY_PASSAGES_TOOL
from athenaeum.synthesize.verifier import verify_passages

_TOOL: dict = {"name": "verify_passages", "input_schema": {}}
_SYSTEM = "You are an independent relevance checker."


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


async def test_no_passages_measures_nothing_and_skips_the_provider() -> None:
    provider = _FakeProvider(None)

    rate, flags = await verify_passages(provider, "claim", [], "model", _SYSTEM, _TOOL)

    assert (rate, flags) == (None, [])
    assert provider.last_messages is None


async def test_checker_failure_is_unmeasured_not_zero() -> None:
    """A checker that returns nothing has judged nothing. Recording 0% would
    publish a failure as if it were a verdict, so the rate is None, and no
    passage is marked as passing."""
    provider = _FakeProvider(None)
    passages = [_passage("Sentence one."), _passage("Sentence two.")]

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert rate is None
    assert flags == [False, False]


async def test_relevance_alone_decides_the_verdict() -> None:
    """A checker that still sends the retired verbatim_plausible field must
    not have it counted either way."""
    passages = [_passage("A"), _passage("B"), _passage("C")]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [
            {"passage_index": 0, "relevant": True, "verbatim_plausible": False},
            {"passage_index": 1, "relevant": False, "verbatim_plausible": True},
            {"passage_index": 2, "relevant": True},
        ]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert flags == [True, False, True]
    assert rate == pytest.approx(2 / 3)


def test_tool_schema_no_longer_asks_about_verbatim_accuracy() -> None:
    item = VERIFY_PASSAGES_TOOL["input_schema"]["properties"]["results"]["items"]

    assert "verbatim_plausible" not in item["properties"]
    assert item["required"] == ["passage_index", "relevant"]


async def test_missing_passage_index_defaults_to_not_relevant() -> None:
    passages = [_passage("A"), _passage("B")]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [{"passage_index": 0, "relevant": True}]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    # Passage 1 was never reported on: it counts as not relevant, rather than
    # being skipped (which would inflate the rate) or crashing the aggregate.
    assert flags == [True, False]
    assert rate == 0.5


async def test_out_of_range_passage_index_is_ignored_not_fatal() -> None:
    passages = [_passage("A")]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [
            {"passage_index": 0, "relevant": True},
            {"passage_index": 7, "relevant": True},
            {"passage_index": -1, "relevant": True},
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
        "input": {"results": [{"passage_index": 0, "relevant": True}]},
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


async def test_relevance_rate_is_the_relevant_fraction() -> None:
    passages = [_passage(f"Sentence {i}.") for i in range(4)]
    provider = _FakeProvider({
        "name": "verify_passages",
        "input": {"results": [
            {"passage_index": 0, "relevant": True},
            {"passage_index": 1, "relevant": True},
            {"passage_index": 2, "relevant": True},
            {"passage_index": 3, "relevant": False},
        ]},
    })

    rate, flags = await verify_passages(provider, "claim", passages, "model", _SYSTEM, _TOOL)

    assert flags.count(True) == 3
    assert rate == 0.75
