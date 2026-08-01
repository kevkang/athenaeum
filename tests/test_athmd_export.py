"""Export-snapshot test for `.athmd` — the worked example from the plan:
two statements, one (Kowalski) cited by both with two distinct quotes, the
other two references (Nakamura, Zhou) each cited by one statement with one
quote apiece. Passage ids are pinned (not uuid4()) so the reference-number
tiebreak — see domain/citations.py's docstring on why StatementEvidence has
no persisted order — is deterministic and this snapshot doesn't flake."""
from __future__ import annotations

import uuid

import pytest

from athenaeum.archive.athmd import build_athmd
from athenaeum.storage.models import (
    ComposeDocument,
    Paper,
    PaperSection,
    PaperSentence,
    Passage,
    Project,
    ProjectPaper,
    Statement,
    StatementEvidence,
    User,
)
from tests.test_citations import _seed_project

QUOTE_KOWALSKI_1 = (
    "the abstraction step proceeds through a radical intermediate confirmed by EPR spectroscopy"
)
QUOTE_KOWALSKI_2 = "similar radical signatures were observed across all tested substrate classes"
QUOTE_NAKAMURA_1 = "EPR linewidths were consistent with the Kowalski et al. assignment"
QUOTE_ZHOU_1 = (
    "under RH < 10%, the measured rate constant deviated by up to 40% from the "
    "radical-mechanism prediction"
)


async def _seed_paper(
    session, owner: User, project: Project, *, title: str, authors: list[str],
    venue: str, year: int, doi: str, sentence_id: uuid.UUID, quote: str,
) -> tuple[Paper, PaperSentence]:
    paper = Paper(
        id=uuid.uuid4(), title=title, authors_json=[{"name": a} for a in authors],
        venue=venue, year=year, doi=doi, status="full_text",
    )
    session.add(paper)
    await session.flush()
    session.add(ProjectPaper(project_id=project.id, paper_id=paper.id, added_by=owner.id))

    section = PaperSection(id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0,
                            char_start=0, char_end=len(quote))
    session.add(section)
    await session.flush()

    sentence = PaperSentence(id=sentence_id, paper_id=paper.id, section_id=section.id,
                              ord=0, text=quote, char_start=0, char_end=len(quote))
    session.add(sentence)
    await session.flush()
    return paper, sentence


async def test_build_athmd_worked_example(db_session) -> None:
    owner, project = await _seed_project(db_session)

    _paper_kowalski, sentence_kowalski_1 = await _seed_paper(
        db_session, owner, project,
        title="Radical mechanisms in atmospheric photochemistry.",
        authors=["Anna Kowalski", "Marek Fabrikant", "Jing Chen"],
        venue="Journal of Physical Chemistry A", year=2019, doi="10.1021/example1",
        sentence_id=uuid.UUID(int=1), quote=QUOTE_KOWALSKI_1,
    )
    _paper_nakamura, sentence_nakamura_1 = await _seed_paper(
        db_session, owner, project,
        title="Spectroscopic evidence for radical pathways in tropospheric oxidation.",
        authors=["Ryo Nakamura", "Priya Singh"],
        venue="Atmospheric Chemistry and Physics", year=2021, doi="10.5194/example2",
        sentence_id=uuid.UUID(int=2), quote=QUOTE_NAKAMURA_1,
    )
    _paper_zhou, sentence_zhou_1 = await _seed_paper(
        db_session, owner, project,
        title="Humidity dependence of hydrogen-abstraction kinetics.",
        authors=["Wei Zhou"],
        venue="Combustion and Flame", year=2023, doi="10.1016/example3",
        sentence_id=uuid.UUID(int=4), quote=QUOTE_ZHOU_1,
    )

    # A second Kowalski sentence/passage for statement 2's distinct quote —
    # pinned id (int=3) so it sorts after Kowalski's first passage (int=1)
    # and before Zhou's (int=4) within statement 2's own evidence set.
    kowalski_paper_id = sentence_kowalski_1.paper_id
    kowalski_section_id = sentence_kowalski_1.section_id
    sentence_kowalski_2 = PaperSentence(
        id=uuid.UUID(int=3), paper_id=kowalski_paper_id, section_id=kowalski_section_id,
        ord=1, text=QUOTE_KOWALSKI_2, char_start=0, char_end=len(QUOTE_KOWALSKI_2),
    )
    db_session.add(sentence_kowalski_2)
    await db_session.flush()

    passage_kowalski_1 = Passage(id=uuid.UUID(int=1), paper_id=kowalski_paper_id,
                                  sentence_ids=[sentence_kowalski_1.id],
                                  verbatim_text=QUOTE_KOWALSKI_1, verified=True)
    passage_kowalski_2 = Passage(id=uuid.UUID(int=3), paper_id=kowalski_paper_id,
                                  sentence_ids=[sentence_kowalski_2.id],
                                  verbatim_text=QUOTE_KOWALSKI_2, verified=True)
    passage_nakamura_1 = Passage(id=uuid.UUID(int=2), paper_id=sentence_nakamura_1.paper_id,
                                  sentence_ids=[sentence_nakamura_1.id],
                                  verbatim_text=QUOTE_NAKAMURA_1, verified=True)
    passage_zhou_1 = Passage(id=uuid.UUID(int=4), paper_id=sentence_zhou_1.paper_id,
                              sentence_ids=[sentence_zhou_1.id],
                              verbatim_text=QUOTE_ZHOU_1, verified=True)
    db_session.add_all(
        [passage_kowalski_1, passage_kowalski_2, passage_nakamura_1, passage_zhou_1]
    )
    await db_session.flush()

    statement_1 = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="hydrogen abstraction proceeds through a radical intermediate",
    )
    statement_2 = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="the reaction rate under low humidity remains contested",
    )
    db_session.add_all([statement_1, statement_2])
    await db_session.flush()
    db_session.add_all([
        StatementEvidence(statement_id=statement_1.id, target_kind="passage",
                           target_id=passage_kowalski_1.id),
        StatementEvidence(statement_id=statement_1.id, target_kind="passage",
                           target_id=passage_nakamura_1.id),
        StatementEvidence(statement_id=statement_2.id, target_kind="passage",
                           target_id=passage_kowalski_2.id),
        StatementEvidence(statement_id=statement_2.id, target_kind="passage",
                           target_id=passage_zhou_1.id),
    ])
    await db_session.flush()

    doc = ComposeDocument(
        id=uuid.uuid4(),
        project_id=project.id,
        content_json={
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1},
                 "content": [{"type": "text", "text": "Discussion"}]},
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "It is now widely accepted that "},
                    {"type": "text", "text": statement_1.assertion, "marks": [
                        {"type": "statementLink", "attrs": {"statementId": str(statement_1.id)}}
                    ]},
                    {"type": "text", "text": ", though "},
                    {"type": "text", "text": statement_2.assertion, "marks": [
                        {"type": "statementLink", "attrs": {"statementId": str(statement_2.id)}}
                    ]},
                    {"type": "text", "text": "."},
                ]},
            ],
        },
        version=1,
    )
    db_session.add(doc)
    await db_session.flush()

    result = await build_athmd(db_session, doc.id)

    expected = (
        "# Discussion\n"
        "\n"
        "It is now widely accepted that "
        f"[hydrogen abstraction proceeds through a radical intermediate]"
        f"(athm:statement/{statement_1.id})[^1.1], [^2.1], though "
        f"[the reaction rate under low humidity remains contested]"
        f"(athm:statement/{statement_2.id})[^1.2], [^3.1]."
        "\n"
        "\n"
        "## References\n"
        "\n"
        "1. Kowalski A, Fabrikant M, Chen J (2019). Radical mechanisms in atmospheric "
        "photochemistry. *Journal of Physical Chemistry A*. https://doi.org/10.1021/example1\n"
        "\n"
        f'   1.1 "{QUOTE_KOWALSKI_1}"\n'
        f'   1.2 "{QUOTE_KOWALSKI_2}"\n'
        "\n"
        "2. Nakamura R, Singh P (2021). Spectroscopic evidence for radical pathways in "
        "tropospheric oxidation. *Atmospheric Chemistry and Physics*. "
        "https://doi.org/10.5194/example2\n"
        "\n"
        f'   2.1 "{QUOTE_NAKAMURA_1}"\n'
        "\n"
        "3. Zhou W (2023). Humidity dependence of hydrogen-abstraction kinetics. "
        "*Combustion and Flame*. https://doi.org/10.1016/example3\n"
        "\n"
        f'   3.1 "{QUOTE_ZHOU_1}"\n'
    )

    assert result == expected


async def test_build_athmd_statement_with_no_evidence_has_no_markers_or_references(
    db_session,
) -> None:
    owner, project = await _seed_project(db_session)
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion="A claim with nothing attached yet.",
    )
    db_session.add(statement)
    await db_session.flush()

    doc = ComposeDocument(
        id=uuid.uuid4(), project_id=project.id, version=1,
        content_json={
            "type": "doc",
            "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "A claim with nothing attached yet.", "marks": [
                    {"type": "statementLink", "attrs": {"statementId": str(statement.id)}}
                ]},
            ]}],
        },
    )
    db_session.add(doc)
    await db_session.flush()

    result = await build_athmd(db_session, doc.id)

    expected = (
        f"[A claim with nothing attached yet.](athm:statement/{statement.id})\n"
    )
    assert result == expected
    assert "## References" not in result


async def test_build_athmd_raises_on_unsupported_block_content(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    doc = ComposeDocument(
        id=uuid.uuid4(), project_id=project.id, version=1,
        # blockquote isn't part of Compose's enabled node set (see
        # ComposeView.tsx's StarterKit.configure) even after Phase 7 added
        # codeBlock/table/link/code — a genuinely unsupported type to prove
        # export still fails loudly rather than silently dropping content.
        content_json={"type": "doc", "content": [{"type": "blockquote", "content": []}]},
    )
    db_session.add(doc)
    await db_session.flush()

    with pytest.raises(ValueError, match="Unsupported"):
        await build_athmd(db_session, doc.id)
