"""Annotations, datasets, and per-passage evaluations (on syntheses)."""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.domain.models import (
    AnnotationCreate,
    AnnotationOut,
    DatasetCreate,
    DatasetOut,
    EvaluationCreate,
    EvaluationOut,
)
from athenaeum.security import decode_session_token
from athenaeum.storage.models import (
    Annotation,
    Dataset,
    Evaluation,
    ProjectMember,
    Synthesis,
    User,
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


_FIGURE_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_ALLOWED_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


@router.post("/datasets/upload", response_model=DatasetOut, status_code=201)
async def upload_figure(
    session: SessionDep,
    user: CurrentUserDep,
    project_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
) -> Dataset:
    """Upload an image and create the `Dataset(kind='figure')` row Compose
    figures point at — modeled directly on `api/papers.py::upload_paper`."""
    settings = get_settings()
    if not settings.s3_bucket:
        raise HTTPException(status_code=501, detail="S3 upload not configured (set S3_BUCKET)")

    suffix = (
        "." + (file.filename or "").rsplit(".", 1)[-1].lower()
        if "." in (file.filename or "") else ""
    )
    if suffix not in _ALLOWED_FIGURE_SUFFIXES:
        raise HTTPException(
            status_code=400, detail="Only PNG, JPEG, GIF, SVG, and WebP images are supported"
        )

    content = await file.read()
    if len(content) > _FIGURE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 20 MB)")

    await _assert_member(session, project_id, user.id)

    dataset_id = uuid.uuid4()
    s3_key = f"figures/{dataset_id}{suffix}"

    from athenaeum.storage.s3 import upload_fileobj
    await upload_fileobj(
        content, settings.s3_bucket, s3_key, settings.aws_region, settings.s3_endpoint_url
    )

    dataset = Dataset(
        id=dataset_id,
        project_id=project_id,
        kind="figure",
        title=title.strip() if title else (file.filename or "Untitled figure"),
        storage_uri=f"s3://{settings.s3_bucket}/{s3_key}",
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.get("/datasets/{dataset_id}/file")
async def get_dataset_file(
    dataset_id: uuid.UUID,
    session: SessionDep,
    token: str = Query(..., description="JWT session token — <img src> can't carry a header"),
) -> RedirectResponse:
    """Redirects to a freshly generated presigned URL — same query-param
    auth as `api/backtrace.py`'s WebSocket stream, for the same reason: the
    browser fetches this directly (as an <img src>), so it can't attach an
    Authorization header the way axios does everywhere else."""
    try:
        user_id = decode_session_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown user")

    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or not dataset.storage_uri:
        raise HTTPException(status_code=404, detail="Figure not found")
    await _assert_member(session, dataset.project_id, user.id)

    if not dataset.storage_uri.startswith("s3://"):
        raise HTTPException(status_code=500, detail="Dataset has no S3-backed file")
    bucket, _, key = dataset.storage_uri.removeprefix("s3://").partition("/")

    settings = get_settings()
    from athenaeum.storage.s3 import generate_presigned_url
    url = await generate_presigned_url(
        bucket, key, region=settings.aws_region,
        endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url,
    )
    return RedirectResponse(url)


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
