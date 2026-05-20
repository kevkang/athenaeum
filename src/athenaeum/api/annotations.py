"""Annotations, questions, datasets, and per-passage evaluations (on syntheses)."""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import (
    AnnotationCreate,
    AnnotationOut,
    DatasetCreate,
    DatasetOut,
    EvaluationCreate,
    EvaluationOut,
    QuestionCreate,
    QuestionOut,
    QuestionUpdate,
)
from athenaeum.storage.models import (
    Annotation,
    Dataset,
    Evaluation,
    ProjectMember,
    Question,
    Synthesis,
)

log = structlog.get_logger()
router = APIRouter(tags=["annotations"])


async def _assert_member(session, project_id, user_id) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Not a project member")


# ── Annotations ────────────────────────────────────────────────────────────────

@router.post("/annotations", response_model=AnnotationOut, status_code=201)
async def create_annotation(
    payload: AnnotationCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Annotation:
    await _assert_member(session, payload.project_id, user.id)
    annotation = Annotation(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        author_id=user.id,
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        body=payload.body,
    )
    session.add(annotation)
    await session.commit()
    await session.refresh(annotation)
    return annotation


@router.get("/annotations", response_model=list[AnnotationOut])
async def list_annotations(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
    target_kind: str | None = Query(default=None),
    target_id: uuid.UUID | None = Query(default=None),
) -> list[Annotation]:
    await _assert_member(session, project_id, user.id)
    stmt = select(Annotation).where(Annotation.project_id == project_id)
    if target_kind is not None:
        stmt = stmt.where(Annotation.target_kind == target_kind)
    if target_id is not None:
        stmt = stmt.where(Annotation.target_id == target_id)
    stmt = stmt.order_by(Annotation.created_at.desc())
    rows = await session.scalars(stmt)
    return list(rows)


@router.delete("/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    annotation = await session.get(Annotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    if annotation.author_id != user.id:
        raise HTTPException(status_code=403, detail="Not the annotation author")
    await session.delete(annotation)
    await session.commit()


# ── Questions ──────────────────────────────────────────────────────────────────

@router.post("/questions", response_model=QuestionOut, status_code=201)
async def create_question(
    payload: QuestionCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Question:
    await _assert_member(session, payload.project_id, user.id)
    question = Question(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        author_id=user.id,
        body=payload.body,
        attached_to=payload.attached_to,
        status="open",
    )
    session.add(question)
    await session.commit()
    await session.refresh(question)
    return question


@router.get("/questions", response_model=list[QuestionOut])
async def list_questions(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
    status: str | None = Query(default=None),
) -> list[Question]:
    await _assert_member(session, project_id, user.id)
    stmt = select(Question).where(Question.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Question.status == status)
    stmt = stmt.order_by(Question.created_at.desc())
    rows = await session.scalars(stmt)
    return list(rows)


@router.patch("/questions/{question_id}", response_model=QuestionOut)
async def update_question(
    question_id: uuid.UUID,
    payload: QuestionUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Question:
    question = await session.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    await _assert_member(session, question.project_id, user.id)
    question.status = payload.status
    await session.commit()
    await session.refresh(question)
    return question


# ── Datasets ───────────────────────────────────────────────────────────────────

@router.post("/datasets", response_model=DatasetOut, status_code=201)
async def create_dataset(
    payload: DatasetCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Dataset:
    await _assert_member(session, payload.project_id, user.id)
    dataset = Dataset(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        kind=payload.kind,
        title=payload.title,
        storage_uri=payload.storage_uri,
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.get("/datasets", response_model=list[DatasetOut])
async def list_datasets(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Query(...),
) -> list[Dataset]:
    await _assert_member(session, project_id, user.id)
    rows = await session.scalars(
        select(Dataset)
        .where(Dataset.project_id == project_id)
        .order_by(Dataset.created_at.desc())
    )
    return list(rows)


# ── Evaluations (per synthesis passage) ───────────────────────────────────────

@router.post(
    "/syntheses/{synthesis_id}/evaluations",
    response_model=EvaluationOut,
    status_code=201,
)
async def upsert_evaluation(
    synthesis_id: uuid.UUID,
    payload: EvaluationCreate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Evaluation:
    synthesis = await session.get(Synthesis, synthesis_id)
    if synthesis is None:
        raise HTTPException(status_code=404, detail="Synthesis not found")
    await _assert_member(session, synthesis.project_id, user.id)

    # Upsert: one evaluation per (synthesis, passage, user)
    existing = await session.scalar(
        select(Evaluation).where(
            Evaluation.synthesis_id == synthesis_id,
            Evaluation.passage_id == payload.passage_id,
            Evaluation.user_id == user.id,
        )
    )
    if existing is not None:
        existing.stance = payload.stance
        existing.body = payload.body
        await session.commit()
        await session.refresh(existing)
        return existing

    evaluation = Evaluation(
        id=uuid.uuid4(),
        synthesis_id=synthesis_id,
        passage_id=payload.passage_id,
        user_id=user.id,
        stance=payload.stance,
        body=payload.body,
    )
    session.add(evaluation)
    await session.commit()
    await session.refresh(evaluation)
    return evaluation
