""".ath version 2 (step 10, D-N3): the Nucleate corpus in the archive, the
published schema, and the round trip through export and import."""
from __future__ import annotations

import copy
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from athenaeum.archive import ath_v2
from athenaeum.archive.export import build_archive, dumps_json
from athenaeum.archive.import_ import archive_version, import_project
from athenaeum.domain import nucleate as v
from athenaeum.domain.relations import SEMANTIC_RELATIONS
from athenaeum.nucleate.corpus import place_node
from athenaeum.storage.models import (
    NodeEvidence,
    NodeReview,
    Nucleus,
    NucleusExpansion,
    NucleusPrompt,
    Paper,
    PaperSection,
    PaperSentence,
    Project,
    ProjectMember,
    ProjectPaper,
    SemanticEdge,
    SemanticEdgeEvidence,
    User,
)

FULL_TEXT_ONLY = "This sentence of the full text is never quoted anywhere."
QUOTE = "We used qSIP following Koch et al. (2018)."


def _selector(exact: str) -> dict[str, str]:
    return {"type": "TextQuoteSelector", "exact": exact, "prefix": "Methods. ", "suffix": " Soils"}


async def _corpus(session):
    """A project with a library paper, a corpus-only paper, a DOI-less
    paper, one nucleus grown by one prompt, reviews in every shape, and two
    relations (one rejected)."""
    user = User(id=uuid.uuid4(), orcid=f"0000-0000-{uuid.uuid4().hex[:4]}-0010",
                display_name="Archivist")
    session.add(user)
    await session.flush()
    project = Project(id=uuid.uuid4(), title="Corpus archive", owner_id=user.id)
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    tag = uuid.uuid4().hex[:8]
    rivera = Paper(id=uuid.uuid4(), title="Deep growth", year=2023, status="full_text",
                   doi=f"10.5555/rivera-{tag}", authors_json=[{"name": "Ana Rivera"}])
    koch = Paper(id=uuid.uuid4(), title="qSIP growth", year=2018, status="ghost",
                 doi=f"10.5555/koch-{tag}", abstract="Growth by qSIP.",
                 authors_json=[{"name": "Benjamin Koch"}])
    nodoi = Paper(id=uuid.uuid4(), title="A preprint with no DOI", year=2024, status="ghost",
                  authors_json=[{"name": "Lee Park"}])
    session.add_all([rivera, koch, nodoi])
    await session.flush()
    session.add(ProjectPaper(project_id=project.id, paper_id=rivera.id, added_by=user.id))
    section = PaperSection(id=uuid.uuid4(), paper_id=rivera.id, label="Methods", ord=0,
                           char_start=0, char_end=200)
    session.add(section)
    await session.flush()
    for i, text in enumerate([QUOTE, FULL_TEXT_ONLY]):
        session.add(PaperSentence(id=uuid.uuid4(), paper_id=rivera.id, section_id=section.id,
                                  ord=i, text=text, char_start=i * 100,
                                  char_end=i * 100 + len(text)))

    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    nucleus = Nucleus(id=uuid.uuid4(), project_id=project.id, question="How fast?",
                      created_by=user.id, created_at=t0)
    session.add(nucleus)
    await session.flush()
    prompt = NucleusPrompt(id=uuid.uuid4(), nucleus_id=nucleus.id, text="How fast?",
                           created_by=user.id, created_at=t0)
    session.add(prompt)
    await session.flush()
    session.add(NucleusExpansion(id=uuid.uuid4(), nucleus_id=nucleus.id, prompt_id=prompt.id,
                                 status=v.DONE, budget=6, model="claude-sonnet-5",
                                 input_tokens=1200, output_tokens=300, reply_text="Found 3.",
                                 started_at=t0, finished_at=t0 + timedelta(minutes=2)))

    a = await place_node(session, nucleus, rivera.id, prompt_id=prompt.id,
                         retrieval_source=v.LIBRARY)
    b = await place_node(session, nucleus, koch.id, from_node_id=a.node.id,
                         prompt_id=prompt.id, retrieval_source=v.OPENALEX)
    c = await place_node(session, nucleus, nodoi.id)          # added by a person
    for i, placed in enumerate((a, b, c)):
        placed.edge.created_at = t0 + timedelta(seconds=10 * (i + 1))
        placed.review.created_at = placed.edge.created_at

    a.review.status = v.ACCEPTED
    a.review.relevance_text = "Rivera et al. measure growth below 1 m."
    a.review.relevance_provenance = v.AI_PROPOSED_ACCEPTED
    a.review.selection_reasoning = "Matched the question's method."
    session.add(NodeEvidence(id=uuid.uuid4(), review_id=a.review.id, ord=0,
                             source=v.FULL_TEXT, verbatim_text=QUOTE,
                             text_quote_selector=_selector(QUOTE)))
    b.review.status = v.REJECTED
    b.review.rejection_reason = "Wrong ecosystem"
    await session.flush()

    kept = SemanticEdge(id=uuid.uuid4(), project_id=project.id, from_node_id=a.node.id,
                        to_node_id=b.node.id, relation="uses_method_of",
                        proposed_relation="extends", provenance=v.AI_PROPOSED_CORRECTED,
                        rationale="A applies B's qSIP.", created_by=user.id,
                        created_at=t0 + timedelta(minutes=5))
    dropped = SemanticEdge(id=uuid.uuid4(), project_id=project.id, from_node_id=a.node.id,
                           to_node_id=c.node.id, relation="supports",
                           proposed_relation="supports", provenance=v.AI_PROPOSED,
                           rejected_at=datetime.now(UTC), rejection_reason="Passing mention",
                           created_at=t0 + timedelta(minutes=6))
    session.add_all([kept, dropped])
    await session.flush()
    session.add(SemanticEdgeEvidence(id=uuid.uuid4(), edge_id=kept.id, ord=0,
                                     source=v.FULL_TEXT, verbatim_text=QUOTE,
                                     text_quote_selector=_selector(QUOTE)))
    await session.flush()
    return user, project, (rivera, koch, nodoi)


# ── Export ───────────────────────────────────────────────────────────────────


async def test_the_export_validates_against_the_published_schema(db_session) -> None:
    _, project, _ = await _corpus(db_session)
    data = json.loads(dumps_json(await build_archive(db_session, project.id)))

    ath_v2.validate(data)
    assert (data["format"], data["version"]) == ("ath", "2.0")
    assert archive_version(data) == 2


async def test_nodes_are_keyed_by_doi_or_by_paper_uuid(db_session) -> None:
    _, project, (rivera, koch, nodoi) = await _corpus(db_session)
    data = await build_archive(db_session, project.id)

    assert [n["id"] for n in data["nodes"]] == [
        f"doi:{rivera.doi}", f"doi:{koch.doi}", f"urn:uuid:{nodoi.id}"]


async def test_the_corpus_carries_results_not_only_prompts(db_session) -> None:
    """Handoff Part VI: prompts are the method, reviewed nodes and evidence the
    data; rejected nodes are kept with their reasons; provenance everywhere."""
    _, project, (rivera, koch, _) = await _corpus(db_session)
    data = await build_archive(db_session, project.id)

    (nucleus,) = data["nuclei"]
    (prompt,) = nucleus["prompts"]
    assert prompt["run"]["status"] == "done" and prompt["run"]["input_tokens"] == 1200

    by_id = {n["id"]: n for n in data["nodes"]}
    (accepted,) = by_id[f"doi:{rivera.doi}"]["reviews"]
    assert accepted["relevance"]["provenance"] == v.AI_PROPOSED_ACCEPTED
    assert accepted["evidence"] == [{"source": "full_text", "selector": _selector(QUOTE)}]
    (rejected,) = by_id[f"doi:{koch.doi}"]["reviews"]
    assert (rejected["status"], rejected["rejection_reason"]) == ("rejected", "Wrong ecosystem")

    lineage = [e for e in data["edges"] if e["kind"] == "lineage"]
    assert [(e["from_kind"], e["retrieval_source"]) for e in lineage] == [
        ("nucleus", "library"), ("node", "openalex"), ("nucleus", None)]
    assert lineage[1]["from"] == f"doi:{rivera.doi}"

    semantic = [e for e in data["edges"] if e["kind"] == "semantic"]
    assert [(e["relation"], e["proposed_relation"], e["provenance"], e["rejected"])
            for e in semantic] == [
        ("uses_method_of", "extends", v.AI_PROPOSED_CORRECTED, False),
        ("supports", "supports", v.AI_PROPOSED, True)]


async def test_corpus_papers_outside_the_library_are_marked(db_session) -> None:
    _, project, (rivera, koch, _) = await _corpus(db_session)
    data = await build_archive(db_session, project.id)

    in_library = {p["id"]: p["in_library"] for p in data["papers"]}
    assert in_library[str(rivera.id)] is True and in_library[str(koch.id)] is False


async def test_no_full_text_leaves_the_server(db_session) -> None:
    _, project, _ = await _corpus(db_session)
    text = dumps_json(await build_archive(db_session, project.id))

    assert QUOTE in text
    assert FULL_TEXT_ONLY not in text


def test_relations_expand_to_cito_iris_through_the_context() -> None:
    ctx = ath_v2.context()
    assert ctx["relation"] == {"@id": "ath:relation", "@type": "@vocab"}
    assert ctx["uses_method_of"] == "http://purl.org/spar/cito/usesMethodIn"
    assert ctx["exact"] == "oa:exact" and ctx["oa"] == "http://www.w3.org/ns/oa#"


# ── Import ───────────────────────────────────────────────────────────────────


async def _reimport(session, project, user, data):
    await session.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
    await session.execute(delete(Project).where(Project.id == project.id))
    await session.commit()
    return await import_project(session, data, user.id)


def _corpus_sections(data):
    return {k: data[k] for k in ("nuclei", "nodes", "edges")}


async def test_the_corpus_survives_export_and_import(db_session) -> None:
    user, project, (rivera, koch, _) = await _corpus(db_session)
    before = json.loads(dumps_json(await build_archive(db_session, project.id)))

    result = await _reimport(db_session, project, user, copy.deepcopy(before))

    after = json.loads(dumps_json(await build_archive(db_session, project.id)))
    assert _corpus_sections(after) == _corpus_sections(before)
    assert result.warnings == []
    # Kept papers go back in the library; corpus-only papers stay out (D-N18).
    library = set(await db_session.scalars(
        select(ProjectPaper.paper_id).where(ProjectPaper.project_id == project.id)))
    assert rivera.id in library and koch.id not in library


async def test_audit_verdicts_are_reported_not_imported(db_session) -> None:
    user, project, _ = await _corpus(db_session)
    data = json.loads(dumps_json(await build_archive(db_session, project.id)))
    review = data["nodes"][0]["reviews"][0]
    review["relevance"]["audit"] = {"supported": True, "rationale": "Quote says so.",
                                    "checked": "2026-09-02T00:00:00+00:00"}

    result = await _reimport(db_session, project, user, data)

    assert any("audit verdict" in w for w in result.warnings)


async def test_a_run_exported_mid_step_imports_cancelled(db_session) -> None:
    user, project, _ = await _corpus(db_session)
    data = json.loads(dumps_json(await build_archive(db_session, project.id)))
    data["nuclei"][0]["prompts"][0]["run"]["status"] = "running"

    result = await _reimport(db_session, project, user, data)

    run = await db_session.scalar(select(NucleusExpansion).join(
        Nucleus, Nucleus.id == NucleusExpansion.nucleus_id).where(
        Nucleus.project_id == project.id))
    assert run is not None and run.status == v.CANCELLED
    assert any("still running" in w for w in result.warnings)


async def test_the_reviews_import_through_lineage(db_session) -> None:
    """Replayed through place_node, so every review has its lineage here too."""
    user, project, _ = await _corpus(db_session)
    data = json.loads(dumps_json(await build_archive(db_session, project.id)))

    await _reimport(db_session, project, user, data)

    statuses = sorted(r.status for r in await db_session.scalars(
        select(NodeReview).join(Nucleus, Nucleus.id == NodeReview.nucleus_id)
        .where(Nucleus.project_id == project.id)))
    assert statuses == ["accepted", "frontier", "rejected"]


@pytest.mark.parametrize(("path", "value", "where"), [
    (("edges", 3, "relation"), "cites", "edges/3"),
    (("nodes", 0, "reviews", 0, "evidence", 0, "selector"), {"type": "TextQuoteSelector"},
     "nodes/0/reviews/0/evidence/0/selector"),
    (("nodes", 0, "id"), "10.5555/no-scheme", "nodes/0/id"),
])
async def test_an_invalid_archive_is_refused_before_anything_is_written(
    db_session, path, value, where
) -> None:
    user, project, _ = await _corpus(db_session)
    data = json.loads(dumps_json(await build_archive(db_session, project.id)))
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    data["project"]["id"] = str(uuid.uuid4())

    with pytest.raises(ath_v2.InvalidArchive, match=where):
        await import_project(db_session, data, user.id)
    assert await db_session.get(Project, uuid.UUID(data["project"]["id"])) is None


# ── Drift guards ─────────────────────────────────────────────────────────────


def _enum(*path: str) -> set[str]:
    node = ath_v2.schema()
    for key in path:
        node = node[key]
    return set(node["enum"])


def test_the_schemas_vocabularies_are_the_codes() -> None:
    defs = ath_v2.schema()["$defs"]
    assert set(defs["relation"]["enum"]) == set(SEMANTIC_RELATIONS)
    assert set(defs["provenance"]["enum"]) == set(v.PROVENANCES)
    assert set(defs["evidence"]["properties"]["source"]["enum"]) == set(v.EVIDENCE_SOURCES)
    node = ath_v2.schema()["properties"]["nodes"]["items"]["properties"]
    assert set(node["fulltext"]["enum"]) == set(v.FULLTEXT_STATES)
    assert set(node["reviews"]["items"]["properties"]["status"]["enum"]) == set(
        v.REVIEW_STATUSES)
    nucleus = ath_v2.schema()["properties"]["nuclei"]["items"]["properties"]
    run = nucleus["prompts"]["items"]["properties"]["run"]["anyOf"][0]
    assert set(run["properties"]["status"]["enum"]) == set(v.EXPANSION_STATUSES)


async def test_the_schema_and_context_are_served(db_session) -> None:
    from athenaeum.api import archive as api

    schema = json.loads((await api.ath_schema()).body)
    context = json.loads((await api.ath_context()).body)
    assert schema["$id"].endswith("/formats/ath/2.0/schema.json")
    assert context["@context"] == ath_v2.context()


async def test_importing_records_that_already_exist_is_a_conflict_not_a_crash(db_session) -> None:
    """The same archive under a new project id: its nuclei are already here."""
    from fastapi import HTTPException, UploadFile

    from athenaeum.api import archive as api

    user, project, _ = await _corpus(db_session)
    data = json.loads(dumps_json(await build_archive(db_session, project.id)))
    data["project"]["id"] = str(uuid.uuid4())
    await db_session.commit()
    upload = UploadFile(file=io.BytesIO(json.dumps(data).encode()),
                        filename="again.ath")

    with pytest.raises(HTTPException) as exc:
        await api.import_project(upload, db_session, user, request=None)  # type: ignore[arg-type]
    assert exc.value.status_code == 409
