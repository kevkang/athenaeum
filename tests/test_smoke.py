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


def test_papers_evidence_sentences_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/papers/00000000-0000-0000-0000-000000000000/evidence-sentences")
    assert resp.status_code == 401


def test_openalex_find_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/openalex/find",
            json={
                "query": "test query for authentication check",
                "project_id": "00000000-0000-0000-0000-000000000000",
            },
        )
    assert resp.status_code == 401


def test_websearch_find_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/websearch/find",
            json={
                "query": "test query for authentication check",
                "project_id": "00000000-0000-0000-0000-000000000000",
            },
        )
    assert resp.status_code == 401


def test_papers_post_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/papers",
            json={"doi": "10.1234/test", "project_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert resp.status_code == 401


def test_statements_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/statements/00000000-0000-0000-0000-000000000000")
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


def test_llm_credentials_get_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/llm/credentials")
    assert resp.status_code == 401


def test_llm_credentials_put_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.put("/llm/credentials", json={"api_key": "fake-key"})
    assert resp.status_code == 401


def test_llm_credentials_delete_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.delete("/llm/credentials")
    assert resp.status_code == 401


def test_zotero_credentials_get_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/zotero/credentials")
    assert resp.status_code == 401


def test_zotero_credentials_put_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.put(
            "/zotero/credentials",
            json={"api_key": "fake-key", "zotero_user_id": "12345"},
        )
    assert resp.status_code == 401


def test_zotero_credentials_delete_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.delete("/zotero/credentials")
    assert resp.status_code == 401


def test_zotero_find_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/zotero/find",
            json={
                "query": "test query for authentication check",
                "project_id": "00000000-0000-0000-0000-000000000000",
                "corpus": "zotero_sync",
            },
        )
    assert resp.status_code == 401


def test_zotero_search_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get(
            "/zotero/search",
            params={"q": "test query", "project_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert resp.status_code == 401


def test_zotero_import_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/zotero/import",
            json={
                "project_id": "00000000-0000-0000-0000-000000000000",
                "zotero_item_key": "ABCD1234",
            },
        )
    assert resp.status_code == 401


def test_zotero_local_status_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/zotero-local/status")
    assert resp.status_code == 401


def test_zotero_local_browse_dirs_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/zotero-local/browse-dirs")
    assert resp.status_code == 401


def test_zotero_local_select_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/zotero-local/select", json={"relative_path": "Zotero"})
    assert resp.status_code == 401


def test_zotero_local_import_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/zotero-local/import",
            json={
                "project_id": "00000000-0000-0000-0000-000000000000",
                "zotero_item_key": "ABCD1234",
            },
        )
    assert resp.status_code == 401


def test_passages_get_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.get("/passages/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401


def test_papers_delete_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.delete(
            "/papers/00000000-0000-0000-0000-000000000000",
            params={"project_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert resp.status_code == 401


def test_statements_lock_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/statements/00000000-0000-0000-0000-000000000000/lock")
    assert resp.status_code == 401


def test_statements_unlock_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post("/statements/00000000-0000-0000-0000-000000000000/unlock")
    assert resp.status_code == 401


def test_statements_lock_all_requires_auth() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/statements/by-project/00000000-0000-0000-0000-000000000000/lock-all"
        )
    assert resp.status_code == 401
