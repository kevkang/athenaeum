"""Unit/DB tests for domain/citations.py — the shared reference numbering
that both the live Compose editor's hover UI and the `.athmd` exporter will
call, so its grouping/numbering logic is validated in isolation first."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.api.compose import (
    export_compose_document,
    get_compose_document_citations,
    save_compose_document,
)
from athenaeum.domain.citations import (
    ReferenceKey,
    build_reference_numbering,
    iter_statement_ids_in_order,
    resolve_evidence_reference,
)
from athenaeum.domain.models import ComposeDocumentUpdate
from athenaeum.storage.models import (
    Dataset,
    Paper,
    PaperSection,
    PaperSentence,
    Passage,
    Project,
    ProjectMember,
    ProjectPaper,
    Statement,
    StatementEvidence,
    User,
)


def test_iter_statement_ids_in_order_dedupes_and_preserves_first_occurrence() -> None:
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    content_json = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "plain text, no mark"},
                    {
                        "type": "text",
                        "text": "first mention of A",
                        "marks": [{"type": "statementLink", "attrs": {"statementId": sid_a}}],
                    },
                ],
            },
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "B appears here",
                        "marks": [
                            {"type": "bold"},
                            {"type": "statementLink", "attrs": {"statementId": sid_b}},
                        ],
                    },
                    {
                        # A split across two text nodes (e.g. partly bold) —
                        # the second occurrence must not duplicate A.
                        "type": "text",
                        "text": "A mentioned again",
                        "marks": [{"type": "statementLink", "attrs": {"statementId": sid_a}}],
                    },
                ],
            },
        ],
    }

    ordered = iter_statement_ids_in_order(content_json)

    assert ordered == [uuid.UUID(sid_a), uuid.UUID(sid_b)]


async def _seed_project(session: AsyncSession) -> tuple[User, Project]:
    owner = User(id=uuid.uuid4(), orcid="0000-0000-0000-0004", display_name="Citations Tester")
    session.add(owner)
    await session.flush()

    project = Project(id=uuid.uuid4(), title="Citations Test Project", owner_id=owner.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
    await session.flush()

    return owner, project


async def _seed_paper_with_sentence(
    session: AsyncSession, owner: User, project: Project, *, title: str, text: str
) -> tuple[Paper, PaperSentence]:
    paper = Paper(
        id=uuid.uuid4(), title=title, authors_json=[{"name": "Ada Author"}],
        venue="Test Journal", year=2020, status="full_text",
    )
    session.add(paper)
    await session.flush()
    session.add(ProjectPaper(project_id=project.id, paper_id=paper.id, added_by=owner.id))

    section = PaperSection(
        id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0, char_start=0, char_end=len(text),
    )
    session.add(section)
    await session.flush()

    sentence = PaperSentence(
        id=uuid.uuid4(), paper_id=paper.id, section_id=section.id, ord=0,
        text=text, char_start=0, char_end=len(text),
    )
    session.add(sentence)
    await session.flush()

    return paper, sentence


async def test_resolve_evidence_reference_paper_has_no_quote(db_session) -> None:
    owner, project = await _seed_project(db_session)
    paper, _sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="General Citation Paper", text="Some sentence."
    )

    key, quote = await resolve_evidence_reference(db_session, "paper", paper.id)

    assert key == ReferenceKey("paper", paper.id)
    assert quote is None


async def test_resolve_evidence_reference_passage_resolves_to_verbatim_text(db_session) -> None:
    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Passage Paper", text="The abstraction step proceeds."
    )
    passage = Passage(
        id=uuid.uuid4(), paper_id=paper.id, sentence_ids=[sentence.id],
        verbatim_text=sentence.text, verified=True, source_check="matches_source",
    )
    db_session.add(passage)
    await db_session.flush()

    key, quote = await resolve_evidence_reference(db_session, "passage", passage.id)

    assert key == ReferenceKey("paper", paper.id)
    assert quote == sentence.text


async def test_resolve_evidence_reference_passage_falls_back_to_sentence(db_session) -> None:
    """Evidence dragged straight from the reader pane points at a
    PaperSentence id, not a synthesis-grade Passage row — same fallback
    api/passages.py::get_passage already implements."""
    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Dragged Sentence Paper", text="A dragged sentence.",
    )

    key, quote = await resolve_evidence_reference(db_session, "passage", sentence.id)

    assert key == ReferenceKey("paper", paper.id)
    assert quote == sentence.text


async def test_resolve_evidence_reference_dataset(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    dataset = Dataset(id=uuid.uuid4(), project_id=project.id, kind="figure", title="Fig. 2")
    db_session.add(dataset)
    await db_session.flush()

    key, quote = await resolve_evidence_reference(db_session, "dataset", dataset.id)

    assert key == ReferenceKey("dataset", dataset.id)
    assert quote is None


async def test_resolve_evidence_reference_statement(db_session) -> None:
    owner, project = await _seed_project(db_session)
    target = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="Radical intermediates are also observed in liquid-phase analogues.",
    )
    db_session.add(target)
    await db_session.flush()

    key, quote = await resolve_evidence_reference(db_session, "statement", target.id)

    assert key == ReferenceKey("statement", target.id)
    assert quote is None


async def test_build_reference_numbering_empty_statement_list(db_session) -> None:
    numbering = await build_reference_numbering(db_session, [])

    assert numbering.references == []
    assert numbering.statement_markers == {}


async def test_build_reference_numbering_statement_with_no_evidence(db_session) -> None:
    owner, project = await _seed_project(db_session)
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="A claim with nothing attached yet.",
    )
    db_session.add(statement)
    await db_session.flush()

    numbering = await build_reference_numbering(db_session, [statement.id])

    assert numbering.references == []
    assert numbering.statement_markers == {statement.id: []}


async def test_build_reference_numbering_general_citation_has_no_quote(db_session) -> None:
    owner, project = await _seed_project(db_session)
    paper, _sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Generally Cited Paper", text="Irrelevant.",
    )
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="Cites a paper generally, no specific passage.",
    )
    db_session.add(statement)
    await db_session.flush()
    db_session.add(
        StatementEvidence(statement_id=statement.id, target_kind="paper", target_id=paper.id)
    )
    await db_session.flush()

    numbering = await build_reference_numbering(db_session, [statement.id])

    assert len(numbering.references) == 1
    ref = numbering.references[0]
    assert ref.number == 1
    assert ref.paper is not None and ref.paper.id == paper.id
    assert len(ref.evidence) == 1
    assert ref.evidence[0].quote is None
    assert numbering.statement_markers[statement.id] == ["1.1"]


async def test_build_reference_numbering_groups_by_reference_and_assigns_subids(db_session) -> None:
    """The worked example from the plan: statement 1 cites paper A (one
    quote); statement 2 cites paper A again (a *different* quote) and paper
    B — paper A ends up as one reference with two evidence sub-ids split
    across the two statements, and paper B is a separate reference."""
    owner, project = await _seed_project(db_session)

    paper_a = Paper(
        id=uuid.uuid4(), title="Paper A", authors_json=[{"name": "Kowalski A"}],
        venue="Journal A", year=2019, status="full_text",
    )
    paper_b = Paper(
        id=uuid.uuid4(), title="Paper B", authors_json=[{"name": "Nakamura R"}],
        venue="Journal B", year=2021, status="full_text",
    )
    db_session.add_all([paper_a, paper_b])
    await db_session.flush()
    db_session.add_all([
        ProjectPaper(project_id=project.id, paper_id=paper_a.id, added_by=owner.id),
        ProjectPaper(project_id=project.id, paper_id=paper_b.id, added_by=owner.id),
    ])

    section_a = PaperSection(id=uuid.uuid4(), paper_id=paper_a.id, label="Body", ord=0,
                              char_start=0, char_end=100)
    section_b = PaperSection(id=uuid.uuid4(), paper_id=paper_b.id, label="Body", ord=0,
                              char_start=0, char_end=100)
    db_session.add_all([section_a, section_b])
    await db_session.flush()

    quote_a1 = "the abstraction step proceeds through a radical intermediate"
    quote_a2 = "similar radical signatures were observed across all tested substrate classes"
    quote_b1 = "EPR linewidths were consistent with the Kowalski et al. assignment"

    sentence_a1 = PaperSentence(id=uuid.uuid4(), paper_id=paper_a.id, section_id=section_a.id,
                                 ord=0, text=quote_a1, char_start=0, char_end=len(quote_a1))
    sentence_a2 = PaperSentence(id=uuid.uuid4(), paper_id=paper_a.id, section_id=section_a.id,
                                 ord=1, text=quote_a2, char_start=0, char_end=len(quote_a2))
    sentence_b1 = PaperSentence(id=uuid.uuid4(), paper_id=paper_b.id, section_id=section_b.id,
                                 ord=0, text=quote_b1, char_start=0, char_end=len(quote_b1))
    db_session.add_all([sentence_a1, sentence_a2, sentence_b1])
    await db_session.flush()

    passage_a1 = Passage(id=uuid.uuid4(), paper_id=paper_a.id, sentence_ids=[sentence_a1.id],
                          verbatim_text=quote_a1, verified=True, source_check="matches_source")
    passage_a2 = Passage(id=uuid.uuid4(), paper_id=paper_a.id, sentence_ids=[sentence_a2.id],
                          verbatim_text=quote_a2, verified=True, source_check="matches_source")
    passage_b1 = Passage(id=uuid.uuid4(), paper_id=paper_b.id, sentence_ids=[sentence_b1.id],
                          verbatim_text=quote_b1, verified=True, source_check="matches_source")
    db_session.add_all([passage_a1, passage_a2, passage_b1])
    await db_session.flush()

    statement_1 = Statement(id=uuid.uuid4(), project_id=project.id, author_id=owner.id,
                             type="claim", assertion="Widely accepted radical mechanism claim.")
    statement_2 = Statement(id=uuid.uuid4(), project_id=project.id, author_id=owner.id,
                             type="claim", assertion="Reaction rate under low humidity claim.")
    db_session.add_all([statement_1, statement_2])
    await db_session.flush()

    db_session.add_all([
        StatementEvidence(
            statement_id=statement_1.id, target_kind="passage", target_id=passage_a1.id
        ),
        StatementEvidence(
            statement_id=statement_2.id, target_kind="passage", target_id=passage_a2.id
        ),
        StatementEvidence(
            statement_id=statement_2.id, target_kind="passage", target_id=passage_b1.id
        ),
    ])
    await db_session.flush()

    numbering = await build_reference_numbering(db_session, [statement_1.id, statement_2.id])

    assert [r.number for r in numbering.references] == [1, 2]
    ref_a, ref_b = numbering.references
    assert ref_a.paper is not None and ref_a.paper.id == paper_a.id
    assert ref_b.paper is not None and ref_b.paper.id == paper_b.id

    # Paper A is one reference with two evidence sub-ids, one per quote.
    assert [e.sub_id for e in ref_a.evidence] == ["1.1", "1.2"]
    assert {e.quote for e in ref_a.evidence} == {quote_a1, quote_a2}

    # Paper B is a separate reference with a single evidence sub-id.
    assert [e.sub_id for e in ref_b.evidence] == ["2.1"]
    assert ref_b.evidence[0].quote == quote_b1

    # Statement 1 only ever cites 1.1; statement 2 cites its own paper-A
    # quote (1.2) and paper B (2.1) — order between the two doesn't matter
    # here, only that both are present exactly once.
    assert numbering.statement_markers[statement_1.id] == ["1.1"]
    assert set(numbering.statement_markers[statement_2.id]) == {"1.2", "2.1"}


async def test_build_reference_numbering_same_evidence_cited_by_two_statements_shares_subid(
    db_session,
) -> None:
    """Two statements citing the literal same passage should share one
    evidence sub-id, not be double-counted as two distinct pieces of
    evidence."""
    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Shared Evidence Paper", text="A shared quote.",
    )
    passage = Passage(id=uuid.uuid4(), paper_id=paper.id, sentence_ids=[sentence.id],
                       verbatim_text=sentence.text, verified=True,
                       source_check="matches_source")
    db_session.add(passage)
    await db_session.flush()

    statement_1 = Statement(id=uuid.uuid4(), project_id=project.id, author_id=owner.id,
                             type="claim", assertion="First statement citing the shared quote.")
    statement_2 = Statement(id=uuid.uuid4(), project_id=project.id, author_id=owner.id,
                             type="claim", assertion="Second statement citing the shared quote.")
    db_session.add_all([statement_1, statement_2])
    await db_session.flush()
    db_session.add_all([
        StatementEvidence(statement_id=statement_1.id, target_kind="passage", target_id=passage.id),
        StatementEvidence(statement_id=statement_2.id, target_kind="passage", target_id=passage.id),
    ])
    await db_session.flush()

    numbering = await build_reference_numbering(db_session, [statement_1.id, statement_2.id])

    assert len(numbering.references) == 1
    assert len(numbering.references[0].evidence) == 1
    assert numbering.statement_markers[statement_1.id] == ["1.1"]
    assert numbering.statement_markers[statement_2.id] == ["1.1"]


async def test_get_compose_document_citations_endpoint_wires_response_correctly(
    db_session,
) -> None:
    """Direct call to the route function (bypassing HTTP/DI, matching this
    suite's existing convention for exercising endpoint logic) — the actual
    dataclass -> Pydantic mapping in api/compose.py has no other coverage."""
    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Endpoint Wiring Paper", text="A quoted sentence.",
    )
    passage = Passage(id=uuid.uuid4(), paper_id=paper.id, sentence_ids=[sentence.id],
                       verbatim_text=sentence.text, verified=True,
                       source_check="matches_source")
    db_session.add(passage)
    await db_session.flush()

    statement = Statement(id=uuid.uuid4(), project_id=project.id, author_id=owner.id,
                           type="claim", assertion="A claim with one citation.")
    db_session.add(statement)
    await db_session.flush()
    db_session.add(
        StatementEvidence(statement_id=statement.id, target_kind="passage", target_id=passage.id)
    )
    await db_session.flush()

    doc = await save_compose_document(
        project.id,
        ComposeDocumentUpdate(
            content_json={
                "type": "doc",
                "content": [{
                    "type": "paragraph",
                    "content": [{
                        "type": "text",
                        "text": "A claim with one citation.",
                        "marks": [
                            {"type": "statementLink", "attrs": {"statementId": str(statement.id)}}
                        ],
                    }],
                }],
            },
            version=1,
        ),
        db_session,
        owner,
    )
    assert doc.version == 2

    result = await get_compose_document_citations(project.id, db_session, owner)

    assert len(result.references) == 1
    ref = result.references[0]
    assert ref.number == 1
    assert ref.kind == "paper"
    assert ref.id == paper.id
    assert ref.paper_title == paper.title
    assert ref.paper_first_author == "Ada Author"
    assert len(ref.evidence) == 1
    assert ref.evidence[0].sub_id == "1.1"
    assert ref.evidence[0].quote == sentence.text
    assert result.statement_markers[statement.id] == ["1.1"]

    response = await export_compose_document(project.id, db_session, owner)
    assert response.media_type == "text/markdown"
    assert ".athmd" in response.headers["Content-Disposition"]
    body = b"".join([chunk async for chunk in response.body_iterator]).decode()
    assert f"(athm:statement/{statement.id})[^1.1]" in body
    assert "## References" in body
    assert sentence.text in body
