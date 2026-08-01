"""Tests for figure upload/read-back (api/annotations.py) — the S3 write
path (`upload_fileobj`) needs a real S3/MinIO endpoint, matching
`upload_paper`'s own lack of coverage for that part, so these focus on what
doesn't need one: validation short-circuits before the S3 call, and
presigned-URL generation, which boto3 computes locally without a network
round-trip (see storage/s3.py::generate_presigned_url's docstring)."""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi import HTTPException, UploadFile

from athenaeum.api.annotations import get_dataset_file, upload_figure
from athenaeum.config import Settings
from athenaeum.security import issue_session_token
from athenaeum.storage.models import Dataset, User
from tests.test_citations import _seed_project


def _fake_settings(**overrides) -> Settings:
    defaults = {
        "s3_bucket": "test-bucket",
        "aws_region": "us-east-1",
        "s3_endpoint_url": "http://localhost:9999",
        "s3_public_endpoint_url": None,
    }
    return Settings(**{**defaults, **overrides})


def _upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


async def test_upload_figure_rejects_unconfigured_s3(db_session, monkeypatch) -> None:
    owner, project = await _seed_project(db_session)
    monkeypatch.setattr(
        "athenaeum.api.annotations.get_settings", lambda: _fake_settings(s3_bucket=None)
    )

    with pytest.raises(HTTPException) as exc_info:
        await upload_figure(
            db_session, owner, project.id, _upload("fig.png", b"\x89PNG\r\n"), None
        )
    assert exc_info.value.status_code == 501


async def test_upload_figure_rejects_unsupported_extension(db_session, monkeypatch) -> None:
    owner, project = await _seed_project(db_session)
    monkeypatch.setattr("athenaeum.api.annotations.get_settings", _fake_settings)

    with pytest.raises(HTTPException) as exc_info:
        await upload_figure(db_session, owner, project.id, _upload("fig.pdf", b"%PDF"), None)
    assert exc_info.value.status_code == 400


async def test_upload_figure_rejects_oversized_file(db_session, monkeypatch) -> None:
    owner, project = await _seed_project(db_session)
    monkeypatch.setattr("athenaeum.api.annotations.get_settings", _fake_settings)

    too_big = b"0" * (20 * 1024 * 1024 + 1)
    with pytest.raises(HTTPException) as exc_info:
        await upload_figure(db_session, owner, project.id, _upload("fig.png", too_big), None)
    assert exc_info.value.status_code == 400


async def test_upload_figure_rejects_non_member(db_session, monkeypatch) -> None:
    _owner, project = await _seed_project(db_session)
    outsider = User(id=uuid.uuid4(), orcid="0000-0000-0000-0093", display_name="Outsider")
    db_session.add(outsider)
    await db_session.flush()

    monkeypatch.setattr("athenaeum.api.annotations.get_settings", _fake_settings)

    with pytest.raises(HTTPException) as exc_info:
        await upload_figure(db_session, outsider, project.id, _upload("fig.png", b"\x89PNG"), None)
    assert exc_info.value.status_code == 403


async def test_get_dataset_file_rejects_invalid_token(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    dataset = Dataset(id=uuid.uuid4(), project_id=project.id, kind="figure", title="Fig 1",
                       storage_uri="s3://test-bucket/figures/fig1.png")
    db_session.add(dataset)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await get_dataset_file(dataset.id, db_session, "not-a-real-token")
    assert exc_info.value.status_code == 401


async def test_get_dataset_file_rejects_non_member(db_session) -> None:
    _owner, project = await _seed_project(db_session)
    outsider = User(id=uuid.uuid4(), orcid="0000-0000-0000-0092", display_name="Outsider 2")
    db_session.add(outsider)
    await db_session.flush()

    dataset = Dataset(id=uuid.uuid4(), project_id=project.id, kind="figure", title="Fig 1",
                       storage_uri="s3://test-bucket/figures/fig1.png")
    db_session.add(dataset)
    await db_session.flush()

    token = issue_session_token(outsider.id)
    with pytest.raises(HTTPException) as exc_info:
        await get_dataset_file(dataset.id, db_session, token)
    assert exc_info.value.status_code == 403


async def test_get_dataset_file_redirects_to_presigned_url(db_session, monkeypatch) -> None:
    owner, project = await _seed_project(db_session)
    dataset = Dataset(id=uuid.uuid4(), project_id=project.id, kind="figure", title="Fig 1",
                       storage_uri="s3://test-bucket/figures/fig1.png")
    db_session.add(dataset)
    await db_session.flush()

    monkeypatch.setattr("athenaeum.api.annotations.get_settings", _fake_settings)
    # generate_presigned_url signs locally (no network round-trip) but still
    # needs *some* credentials present to compute a signature with.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    token = issue_session_token(owner.id)

    response = await get_dataset_file(dataset.id, db_session, token)

    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://localhost:9999/test-bucket/figures/fig1.png")


async def test_get_dataset_file_uses_public_endpoint_when_set(db_session, monkeypatch) -> None:
    """The whole reason s3_public_endpoint_url exists: local MinIO dev signs
    against the docker-internal host by default, which a browser can't
    resolve — this must override that for the URL actually handed back."""
    owner, project = await _seed_project(db_session)
    dataset = Dataset(id=uuid.uuid4(), project_id=project.id, kind="figure", title="Fig 1",
                       storage_uri="s3://test-bucket/figures/fig1.png")
    db_session.add(dataset)
    await db_session.flush()

    monkeypatch.setattr(
        "athenaeum.api.annotations.get_settings",
        lambda: _fake_settings(
            s3_endpoint_url="http://minio:9000", s3_public_endpoint_url="http://localhost:9000"
        ),
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    token = issue_session_token(owner.id)

    response = await get_dataset_file(dataset.id, db_session, token)

    location = response.headers["location"]
    assert location.startswith("http://localhost:9000/")
    assert "minio:9000" not in location
