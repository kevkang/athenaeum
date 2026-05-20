from fastapi.testclient import TestClient

from athenaeum.main import app


def test_health() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_projects_require_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/projects/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


def test_papers_get_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/papers/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


def test_papers_post_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/papers",
            json={"doi": "10.1234/test", "project_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert resp.status_code == 401


def test_bricks_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/bricks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


def test_syntheses_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/syntheses",
            json={
                "claim_text": "test claim for authentication check",
                "project_id": "00000000-0000-0000-0000-000000000000",
            },
        )
    assert resp.status_code == 401


def test_syntheses_patch_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.patch(
            "/syntheses/00000000-0000-0000-0000-000000000000",
            json={"synthesis_text": "My written synthesis."},
        )
    assert resp.status_code == 401


def test_search_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/search",
            json={
                "query": "test query",
                "project_id": "00000000-0000-0000-0000-000000000000",
            },
        )
    assert resp.status_code == 401
