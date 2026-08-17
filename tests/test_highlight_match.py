"""Unit tests for the fuzzy annotation-to-sentence matcher used to import
Zotero highlights — no Zotero involved here, just the window-growing
heuristic against a paper's own sentence-parsed text."""
import uuid

from athenaeum.ingest.highlight_match import match_annotation_to_sentences
from athenaeum.storage.models import Paper, PaperSection, PaperSentence


async def _seed_paper(session, *sentences: str) -> tuple[Paper, list[PaperSentence]]:
    paper = Paper(
        id=uuid.uuid4(), title="Match Test Paper", authors_json=[{"name": "Ada Author"}],
        venue="Test Journal", year=2020, status="full_text",
    )
    session.add(paper)
    await session.flush()

    section = PaperSection(
        id=uuid.uuid4(), paper_id=paper.id, label="Body", ord=0, char_start=0, char_end=1,
    )
    session.add(section)
    await session.flush()

    rows = []
    offset = 0
    for i, text in enumerate(sentences):
        row = PaperSentence(
            id=uuid.uuid4(), paper_id=paper.id, section_id=section.id, ord=i,
            text=text, char_start=offset, char_end=offset + len(text),
        )
        session.add(row)
        rows.append(row)
        offset += len(text) + 1
    await session.flush()
    return paper, rows


async def test_exact_single_sentence_match(db_session) -> None:
    paper, sentences = await _seed_paper(
        db_session,
        "Vents host chemosynthetic bacteria.",
        "These bacteria fix carbon without sunlight.",
        "Tubeworms rely on this symbiosis entirely.",
    )
    match = await match_annotation_to_sentences(
        db_session, paper.id, "These bacteria fix carbon without sunlight."
    )
    assert match is not None
    assert match.sentence_ids == [sentences[1].id]
    assert match.verbatim_text == "These bacteria fix carbon without sunlight."
    assert match.ratio > 0.99


async def test_reflowed_whitespace_still_matches(db_session) -> None:
    paper, sentences = await _seed_paper(
        db_session, "Hydrothermal fluid mixes with cold seawater rapidly."
    )
    # Zotero's own PDF extraction often reflows line breaks differently than
    # Athenaeum's own parse -- normalization should absorb that without
    # needing an exact string match.
    match = await match_annotation_to_sentences(
        db_session, paper.id, "Hydrothermal fluid  mixes with\ncold seawater rapidly."
    )
    assert match is not None
    assert match.sentence_ids == [sentences[0].id]


async def test_multi_sentence_span_grows_window(db_session) -> None:
    paper, sentences = await _seed_paper(
        db_session,
        "Background on the site.",
        "Chemosynthesis sustains the community.",
        "Symbiotic bacteria provide the energy.",
        "Unrelated closing remark.",
    )
    match = await match_annotation_to_sentences(
        db_session, paper.id,
        "Chemosynthesis sustains the community. Symbiotic bacteria provide the energy.",
    )
    assert match is not None
    assert match.sentence_ids == [sentences[1].id, sentences[2].id]


async def test_unrelated_text_returns_no_match(db_session) -> None:
    paper, _sentences = await _seed_paper(db_session, "The sky is blue on a clear day.")
    match = await match_annotation_to_sentences(
        db_session, paper.id, "Quantum entanglement violates local realism."
    )
    assert match is None


async def test_paper_with_no_sentences_returns_no_match(db_session) -> None:
    paper = Paper(id=uuid.uuid4(), title="Empty Paper", authors_json=None, status="full_text")
    db_session.add(paper)
    await db_session.flush()
    match = await match_annotation_to_sentences(db_session, paper.id, "Anything at all.")
    assert match is None
