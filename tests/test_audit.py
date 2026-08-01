"""Tests for Audit mode: the per-statement evidence-supports-claim checker
(audit_evidence, mirroring test_verifier.py's fake-provider pattern for
verify_passages) and the read-side API route that assembles a persisted
AuditRun's findings plus its live-computed "no evidence" statement list."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from athenaeum.api.audit import get_audit_run
from athenaeum.domain.models import ComposeDocumentUpdate
from athenaeum.provider.base import Message
from athenaeum.storage.models import AuditFinding, AuditRun, Statement, StatementEvidence, User
from athenaeum.synthesize.audit import audit_evidence
from tests.test_citations import _seed_paper_with_sentence, _seed_project


class _FakeProvider:
    def __init__(self, tool_result: dict | None) -> None:
        self._tool_result = tool_result
        self.last_messages: list[Message] | None = None
        self.last_system: str | None = None

    async def complete(self, messages, system, max_tokens=4096, tools=None):
        self.last_messages = messages
        self.last_system = system
        return None, self._tool_result


# ── audit_evidence ───────────────────────────────────────────────────────────────

async def test_audit_evidence_empty_quotes_short_circuits_without_calling_the_provider() -> None:
    provider = _FakeProvider(None)

    results = await audit_evidence(provider, "claim", [], "model")

    assert results == []
    assert provider.last_messages is None


async def test_audit_evidence_no_tool_result_marks_everything_unsupported() -> None:
    provider = _FakeProvider(None)

    results = await audit_evidence(provider, "claim", ["quote one", "quote two"], "model")

    assert len(results) == 2
    assert all(supported is False for supported, _rationale in results)
    assert all(rationale for _supported, rationale in results)


async def test_audit_evidence_returns_per_item_verdict_and_rationale() -> None:
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {"results": [
            {"evidence_index": 0, "supported": True, "rationale": "Directly states the claim."},
            {
                "evidence_index": 1, "supported": False,
                "rationale": "Talks about a related but different mechanism.",
            },
        ]},
    })

    results = await audit_evidence(
        provider, "Radical intermediates form during abstraction.",
        ["quote supporting it", "quote about something else"], "model",
    )

    assert results == [
        (True, "Directly states the claim."),
        (False, "Talks about a related but different mechanism."),
    ]


async def test_audit_evidence_missing_index_defaults_to_unsupported() -> None:
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {
            "results": [{"evidence_index": 0, "supported": True, "rationale": "Good match."}],
        },
    })

    results = await audit_evidence(provider, "claim", ["quote A", "quote B"], "model")

    assert results[0] == (True, "Good match.")
    assert results[1][0] is False


async def test_audit_evidence_out_of_range_index_is_ignored_not_fatal() -> None:
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {"results": [
            {"evidence_index": 0, "supported": True, "rationale": "Fine."},
            {"evidence_index": 5, "supported": True, "rationale": "Should be dropped."},
        ]},
    })

    results = await audit_evidence(provider, "claim", ["quote A"], "model")

    assert results == [(True, "Fine.")]


async def test_audit_evidence_context_excludes_which_statement_selected_it() -> None:
    """Same independent-checker isolation property test_verifier.py enforces
    for Synthesis: the auditor sees the claim and the quote, nothing about
    how or why the quote was originally attached."""
    provider = _FakeProvider({
        "name": "audit_evidence",
        "input": {"results": [{"evidence_index": 0, "supported": True, "rationale": "ok"}]},
    })

    await audit_evidence(
        provider, "Melatonin reduces oxidative stress in aged mitochondria.",
        ["EPR spectroscopy confirmed the radical intermediate."], "model",
    )

    assert provider.last_messages is not None
    sent_text = "\n".join(m.content for m in provider.last_messages)
    assert "EPR spectroscopy confirmed the radical intermediate." in sent_text
    assert "Melatonin reduces oxidative stress in aged mitochondria." in sent_text


# ── GET /audit-runs/{id} ─────────────────────────────────────────────────────────

async def _seed_statement(db_session, owner: User, project, assertion: str) -> Statement:
    statement = Statement(
        id=uuid.uuid4(), project_id=project.id, author_id=owner.id, type="claim",
        assertion=assertion,
    )
    db_session.add(statement)
    await db_session.flush()
    return statement


def _doc_content(statement_ids: list[uuid.UUID]) -> dict:
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": f"statement {i}",
                    "marks": [{"type": "statementLink", "attrs": {"statementId": str(sid)}}],
                }],
            }
            for i, sid in enumerate(statement_ids)
        ],
    }


async def test_get_audit_run_returns_404_for_unknown_run(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_audit_run(uuid.uuid4(), db_session, None)  # type: ignore[arg-type]
    assert exc_info.value.status_code == 404


async def test_get_audit_run_rejects_non_member(db_session) -> None:
    from athenaeum.api.compose import save_compose_document

    owner, project = await _seed_project(db_session)
    outsider = User(id=uuid.uuid4(), orcid="0000-0000-0000-0099", display_name="Outsider")
    db_session.add(outsider)
    await db_session.flush()

    doc = await save_compose_document(
        project.id, ComposeDocumentUpdate(content_json=_doc_content([]), version=1),
        db_session, owner,
    )
    audit_run = AuditRun(
        id=uuid.uuid4(), project_id=project.id, compose_document_id=doc.id,
        created_by=owner.id, status="done",
    )
    db_session.add(audit_run)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await get_audit_run(audit_run.id, db_session, outsider)
    assert exc_info.value.status_code == 403


async def test_get_audit_run_assembles_findings_and_no_evidence_statements(db_session) -> None:
    from athenaeum.api.compose import save_compose_document

    owner, project = await _seed_project(db_session)
    paper, sentence = await _seed_paper_with_sentence(
        db_session, owner, project, title="Audited Paper", text="A quoted sentence.",
    )
    from athenaeum.storage.models import Passage
    passage = Passage(id=uuid.uuid4(), paper_id=paper.id, sentence_ids=[sentence.id],
                       verbatim_text=sentence.text, verified=True)
    db_session.add(passage)
    await db_session.flush()

    cited_statement = await _seed_statement(db_session, owner, project, "A cited claim.")
    bare_statement = await _seed_statement(db_session, owner, project, "A claim with no evidence.")
    db_session.add(
        StatementEvidence(
            statement_id=cited_statement.id, target_kind="passage", target_id=passage.id,
        )
    )
    await db_session.flush()

    doc = await save_compose_document(
        project.id,
        ComposeDocumentUpdate(
            content_json=_doc_content([cited_statement.id, bare_statement.id]), version=1,
        ),
        db_session, owner,
    )

    audit_run = AuditRun(
        id=uuid.uuid4(), project_id=project.id, compose_document_id=doc.id,
        created_by=owner.id, status="done", model="test-model",
    )
    db_session.add(audit_run)
    await db_session.flush()
    db_session.add(AuditFinding(
        id=uuid.uuid4(), audit_run_id=audit_run.id, ord=0, statement_id=cited_statement.id,
        sub_id="1.1", target_kind="passage", target_id=passage.id, quote=sentence.text,
        supported=True, rationale="Matches the claim exactly.",
    ))
    await db_session.flush()

    result = await get_audit_run(audit_run.id, db_session, owner)

    assert result.run.id == audit_run.id
    assert result.run.status == "done"
    assert len(result.findings) == 1
    assert result.findings[0].sub_id == "1.1"
    assert result.findings[0].supported is True
    assert result.statements_with_no_evidence == [bare_statement.id]
