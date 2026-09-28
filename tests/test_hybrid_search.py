"""Tests for hybrid retrieval against a real database — pgvector cosine
search and Postgres full-text search both need a live Postgres (FTS in
particular can't be meaningfully faked: it depends on the `text_search`
generated column and `plainto_tsquery`)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.retrieve.hybrid import _rrf_merge, search
from athenaeum.storage.models import Paper, PaperSection, PaperSentence, Project, ProjectPaper, User

_DIM = 768


def _one_hot(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


async def _seed_project_with_sentences(
    session: AsyncSession, sentences: list[tuple[str, list[float] | None]]
) -> tuple[uuid.UUID, Paper, uuid.UUID]:
    """Each item is (text, embedding-or-None). Returns (project_id, paper, owner_id)."""
    owner = User(id=uuid.uuid4(), orcid=str(uuid.uuid4())[:19], display_name="Owner")
    session.add(owner)
    await session.flush()

    project = Project(id=uuid.uuid4(), title="Hybrid Search Test", owner_id=owner.id)
    session.add(project)
    paper = Paper(id=uuid.uuid4(), title="A Paper", status="full_text")
    session.add(paper)
    await session.flush()

    session.add(ProjectPaper(project_id=project.id, paper_id=paper.id, added_by=owner.id))
    section = PaperSection(
        id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0, char_start=0, char_end=1000,
    )
    session.add(section)
    await session.flush()

    for i, (text, embedding) in enumerate(sentences):
        session.add(PaperSentence(
            id=uuid.uuid4(), paper_id=paper.id, section_id=section.id, ord=i,
            text=text, char_start=i * 100, char_end=i * 100 + len(text), embedding=embedding,
        ))
    await session.flush()
    await session.commit()
    return project.id, paper, owner.id


async def test_text_mode_finds_matching_sentences(db_session: AsyncSession) -> None:
    project_id, _paper, _owner_id = await _seed_project_with_sentences(db_session, [
        ("Melatonin reduces oxidative stress in aged mitochondria.", None),
        ("Sea levels are rising due to glacial melt.", None),
    ])

    hits = await search(db_session, project_id, "melatonin oxidative stress", None, mode="text")

    assert len(hits) == 1
    assert "Melatonin" in hits[0].text


async def test_search_is_scoped_to_project(db_session: AsyncSession) -> None:
    project_a, _, _ = await _seed_project_with_sentences(db_session, [
        ("A shared search term appears here.", None),
    ])
    project_b, _, _ = await _seed_project_with_sentences(db_session, [
        ("A shared search term appears here too.", None),
    ])

    hits_a = await search(db_session, project_a, "shared search term", None, mode="text")

    assert len(hits_a) == 1
    assert hits_a[0].paper.id != project_b  # sanity: didn't accidentally cross-match


async def test_vector_mode_ranks_by_cosine_similarity(db_session: AsyncSession) -> None:
    project_id, _paper, _owner_id = await _seed_project_with_sentences(db_session, [
        ("The closely matching sentence.", _one_hot(0)),
        ("The unrelated sentence.", _one_hot(1)),
    ])

    hits = await search(db_session, project_id, "", _one_hot(0), mode="vector", top_k=2)

    assert len(hits) == 2
    assert hits[0].text == "The closely matching sentence."
    assert hits[0].score > hits[1].score


async def test_paper_id_narrows_search_to_one_paper(db_session: AsyncSession) -> None:
    project_id, paper1, owner_id = await _seed_project_with_sentences(db_session, [
        ("A findable claim about mitochondria.", None),
    ])
    # A second paper in the same project with the same term.
    paper2 = Paper(id=uuid.uuid4(), title="Another Paper", status="full_text")
    db_session.add(paper2)
    await db_session.flush()
    db_session.add(ProjectPaper(project_id=project_id, paper_id=paper2.id, added_by=owner_id))
    section2 = PaperSection(
        id=uuid.uuid4(), paper_id=paper2.id, label="Body", ord=0, char_start=0, char_end=1000,
    )
    db_session.add(section2)
    await db_session.flush()
    db_session.add(PaperSentence(
        id=uuid.uuid4(), paper_id=paper2.id, section_id=section2.id, ord=0,
        text="A findable claim about mitochondria.", char_start=0, char_end=40,
    ))
    await db_session.commit()

    hits = await search(
        db_session, project_id, "findable claim mitochondria", None,
        mode="text", paper_id=paper1.id,
    )

    assert len(hits) == 1
    assert hits[0].paper.id == paper1.id


def test_rrf_merge_favors_items_ranked_highly_in_multiple_lists() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    merged = _rrf_merge([a, b, c], [b, a, c])

    ids_in_order = [id_ for id_, _score in merged]
    # a and b each rank #1 in one list and #2 in the other — both should
    # outrank c, which is #3 in both.
    assert ids_in_order.index(a) < ids_in_order.index(c)
    assert ids_in_order.index(b) < ids_in_order.index(c)


def test_rrf_merge_handles_items_only_in_one_list() -> None:
    a = uuid.uuid4()

    merged = _rrf_merge([a], [])

    assert merged == [(a, 1 / 61)]


# ── One paper, without the project link (Nucleate's corpus papers) ────────────


async def _unlinked_paper(session: AsyncSession, text: str, embedding: list[float]) -> Paper:
    """A paper with sentences that is *not* in any project's library — the
    shape of a paper the archivist found on OpenAlex and then ingested."""
    paper = Paper(id=uuid.uuid4(), title="Corpus-only paper", status="full_text")
    session.add(paper)
    await session.flush()
    section = PaperSection(id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0,
                           char_start=0, char_end=1000)
    session.add(section)
    await session.flush()
    session.add(PaperSentence(id=uuid.uuid4(), paper_id=paper.id, section_id=section.id, ord=0,
                              text=text, char_start=0, char_end=len(text), embedding=embedding))
    await session.flush()
    return paper


async def test_an_unlinked_paper_is_invisible_to_a_project_search(db_session) -> None:
    project_id, _, _ = await _seed_project_with_sentences(db_session, [("Unrelated.", None)])
    paper = await _unlinked_paper(db_session, "Growth rates spanned two orders.", _one_hot(7))

    linked = await search(db_session, project_id, "growth rates", _one_hot(7), paper_id=paper.id)

    assert linked == [], "the default scoping is unchanged"


async def test_one_paper_can_be_searched_without_the_project_link(db_session) -> None:
    project_id, _, _ = await _seed_project_with_sentences(db_session, [("Unrelated.", None)])
    paper = await _unlinked_paper(db_session, "Growth rates spanned two orders.", _one_hot(7))

    for mode in ("text", "vector", "hybrid"):
        hits = await search(db_session, project_id, "growth rates", _one_hot(7), mode=mode,
                            paper_id=paper.id, link_to_project=False)
        assert [h.paper.id for h in hits] == [paper.id], mode


async def test_unlinked_search_stays_inside_the_one_paper(db_session) -> None:
    project_id, _, _ = await _seed_project_with_sentences(db_session, [("Unrelated.", None)])
    target = await _unlinked_paper(db_session, "Growth rates in soil.", _one_hot(3))
    await _unlinked_paper(db_session, "Growth rates in lakes.", _one_hot(3))

    hits = await search(db_session, project_id, "growth rates", _one_hot(3),
                        paper_id=target.id, link_to_project=False)

    assert {h.paper.id for h in hits} == {target.id}


async def test_unscoped_without_a_paper_is_refused(db_session) -> None:
    """That combination would search every paper on the server."""
    import pytest

    with pytest.raises(ValueError, match="requires a paper_id"):
        await search(db_session, uuid.uuid4(), "anything", None, link_to_project=False)
