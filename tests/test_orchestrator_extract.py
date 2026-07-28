"""Tests for orchestrator._step_extract against a real database — the seam
where retrieved sentences become Passage rows. Exercises the
verbatim-contiguity fix end-to-end (non-adjacent sentence_ids must be
rejected, not silently joined into a misleading "verbatim" quote), not just
the pure is_contiguous() helper in isolation.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.models import PaperSnippet, SentenceHit
from athenaeum.provider.base import Message
from athenaeum.storage.models import Paper, PaperSection, PaperSentence
from athenaeum.synthesize.orchestrator import _step_extract


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        return None, self._tool_result


async def _seed_sentences(
    session: AsyncSession, texts: list[str]
) -> tuple[Paper, list[PaperSentence]]:
    paper = Paper(id=uuid.uuid4(), title="A Test Paper", status="full_text")
    session.add(paper)
    await session.flush()

    section = PaperSection(
        id=uuid.uuid4(), paper_id=paper.id, label="Results", ord=0, char_start=0, char_end=1000,
    )
    session.add(section)
    await session.flush()

    sentences = [
        PaperSentence(
            id=uuid.uuid4(), paper_id=paper.id, section_id=section.id,
            ord=i, text=text, char_start=i * 100, char_end=i * 100 + len(text),
        )
        for i, text in enumerate(texts)
    ]
    session.add_all(sentences)
    await session.flush()
    return paper, sentences


def _hit(paper: Paper, sentence: PaperSentence) -> SentenceHit:
    return SentenceHit(
        sentence_id=sentence.id, text=sentence.text,
        char_start=sentence.char_start, char_end=sentence.char_end,
        section_label="Results", score=0.9,
        paper=PaperSnippet(id=paper.id, title=paper.title, doi=None, year=None, venue=None),
    )


async def test_contiguous_selection_becomes_one_joined_passage(db_session: AsyncSession) -> None:
    paper, sentences = await _seed_sentences(db_session, [
        "Melatonin reduces oxidative stress.",
        "This effect was observed in aged mitochondria.",
        "No effect was seen in young tissue.",
    ])
    hits = [_hit(paper, s) for s in sentences]
    provider = _FakeProvider({
        "name": "extract_passages",
        "input": {"passages": [{
            "sentence_ids": [str(sentences[0].id), str(sentences[1].id)],
            "paper_id": str(paper.id),
            "rationale": "Directly states the causal claim.",
        }]},
    })

    passages = await _step_extract(provider, "melatonin reduces oxidative stress", hits, db_session)

    assert len(passages) == 1
    assert passages[0].verbatim_text == (
        "Melatonin reduces oxidative stress. This effect was observed in aged mitochondria."
    )
    assert passages[0].sentence_ids == [sentences[0].id, sentences[1].id]


async def test_non_contiguous_selection_is_rejected_not_spliced(db_session: AsyncSession) -> None:
    """The sentence in between (index 1) is a caveat the extractor did NOT
    select — joining sentence 0 directly to sentence 2 would read as one
    continuous quote while silently dropping it. Must be rejected, not
    published as a spliced passage."""
    paper, sentences = await _seed_sentences(db_session, [
        "Melatonin reduces oxidative stress.",
        "However, this was only tested in vitro.",
        "The effect size was large.",
    ])
    hits = [_hit(paper, s) for s in sentences]
    provider = _FakeProvider({
        "name": "extract_passages",
        "input": {"passages": [{
            "sentence_ids": [str(sentences[0].id), str(sentences[2].id)],
            "paper_id": str(paper.id),
            "rationale": "Combines the claim and its effect size.",
        }]},
    })

    passages = await _step_extract(provider, "melatonin reduces oxidative stress", hits, db_session)

    assert passages == []


async def test_sentence_ids_outside_the_hits_are_dropped(db_session: AsyncSession) -> None:
    paper, sentences = await _seed_sentences(db_session, ["Melatonin reduces oxidative stress."])
    hits = [_hit(paper, sentences[0])]
    provider = _FakeProvider({
        "name": "extract_passages",
        "input": {"passages": [{
            "sentence_ids": [str(uuid.uuid4())],  # never retrieved
            "paper_id": str(paper.id),
        }]},
    })

    passages = await _step_extract(provider, "claim", hits, db_session)

    assert passages == []
