"""The automatic audit of relevance statements, as read back for display and
export."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.domain.models import CanvasAuditOut
from athenaeum.storage.models import AuditFinding, AuditRun


async def latest_audits(
    session: AsyncSession, review_ids: list[uuid.UUID]
) -> dict[uuid.UUID, CanvasAuditOut]:
    """Each review's most recent automatic audit, derived rather than stored
    (D-N13). A statement counts as supported when any of its quotes supports
    it: each quote was chosen to support the statement on its own."""
    if not review_ids:
        return {}
    rows = (await session.execute(
        select(AuditFinding, AuditRun.created_at)
        .join(AuditRun, AuditRun.id == AuditFinding.audit_run_id)
        .where(AuditFinding.node_review_id.in_(review_ids))
        .order_by(AuditRun.created_at.desc(), AuditFinding.ord)
    )).all()
    latest_run: dict[uuid.UUID, uuid.UUID] = {}
    findings: dict[uuid.UUID, list[AuditFinding]] = {}
    checked_at: dict[uuid.UUID, datetime] = {}
    for finding, created in rows:
        review_id = finding.node_review_id
        assert review_id is not None
        if review_id not in latest_run:
            latest_run[review_id] = finding.audit_run_id
            checked_at[review_id] = created
        if finding.audit_run_id == latest_run[review_id]:
            findings.setdefault(review_id, []).append(finding)

    audits: dict[uuid.UUID, CanvasAuditOut] = {}
    for review_id, fs in findings.items():
        supporting = [f for f in fs if f.supported]
        chosen = supporting[0] if supporting else fs[0]
        audits[review_id] = CanvasAuditOut(
            supported=bool(supporting), rationale=chosen.rationale,
            checked_at=checked_at[review_id],
        )
    return audits
