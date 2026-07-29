"""Tests for the ORCID OAuth login/callback flow — previously had zero
coverage. Focused on the origin-validation behavior specifically: every PR
preview shares the single production API (see .github/workflows/preview.yml),
so without validating and threading the originating frontend's URL through
the flow, every login redirects back to the one fixed production frontend
URL regardless of where the user actually started — previews could never
complete login. See api/auth.py's module-level comment for the full story.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from athenaeum.api.auth import _state_store, callback, login
from athenaeum.config import Settings


@pytest.fixture(autouse=True)
def _orcid_settings(monkeypatch):
    # get_settings() is @lru_cache'd (a process-wide singleton) — whichever
    # test runs first across the *whole* suite locks in real-environment
    # settings for every test after it, so monkeypatch.setenv here would
    # only work if this file happened to run before anything else that
    # calls get_settings(). Patching the name auth.py actually calls
    # sidesteps the cache entirely instead of fighting it.
    settings = Settings(
        orcid_client_id="test-client-id",
        orcid_client_secret="test-client-secret",
        frontend_url="https://athenaindex.app",
    )
    monkeypatch.setattr("athenaeum.api.auth.get_settings", lambda: settings)


def _fake_token_exchange(orcid: str = "0000-0000-0000-0001", name: str = "Ada Tester"):
    async def _post(self, url, data=None, headers=None):
        return httpx.Response(
            200, json={"orcid": orcid, "name": name}, request=httpx.Request("POST", url)
        )
    return _post


# ── login() — origin validation ─────────────────────────────────────────────

async def test_login_with_no_origin_stores_fallback_frontend_url() -> None:
    resp = await login(origin=None)

    assert resp.status_code == 307
    state = resp.headers["location"].split("state=")[1].split("&")[0]
    assert _state_store.pop(state) == "https://athenaindex.app"


async def test_login_with_valid_preview_origin_stores_it() -> None:
    resp = await login(origin="https://pr-9.athenaindex.app")

    state = resp.headers["location"].split("state=")[1].split("&")[0]
    assert _state_store.pop(state) == "https://pr-9.athenaindex.app"


async def test_login_with_valid_localhost_origin_stores_it() -> None:
    resp = await login(origin="http://localhost:5173")

    state = resp.headers["location"].split("state=")[1].split("&")[0]
    assert _state_store.pop(state) == "http://localhost:5173"


@pytest.mark.parametrize("malicious_origin", [
    "https://evil.com",
    "https://athenaindex.app.evil.com",
    "https://pr-9.athenaindex.app.evil.com",
    "javascript:alert(1)",
    "https://pr-nine.athenaindex.app",  # not \d+
])
async def test_login_rejects_unrecognized_origins(malicious_origin: str) -> None:
    """The whole point of the allowlist — an origin that doesn't match the
    known shape must never be honored, or this becomes an open redirect."""
    resp = await login(origin=malicious_origin)

    state = resp.headers["location"].split("state=")[1].split("&")[0]
    assert _state_store.pop(state) == "https://athenaindex.app"


# ── callback() — redirect target follows the validated origin ──────────────

async def test_callback_redirects_to_the_origin_login_recorded(db_session, monkeypatch) -> None:
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_token_exchange())

    login_resp = await login(origin="https://pr-9.athenaindex.app")
    state = login_resp.headers["location"].split("state=")[1].split("&")[0]

    callback_resp = await callback(code="fake-code", state=state, session=db_session)

    assert callback_resp.headers["location"].startswith("https://pr-9.athenaindex.app/auth/callback?")


async def test_callback_falls_back_when_no_origin_given(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "post", _fake_token_exchange(orcid="0000-0000-0000-0002")
    )

    login_resp = await login(origin=None)
    state = login_resp.headers["location"].split("state=")[1].split("&")[0]

    callback_resp = await callback(code="fake-code", state=state, session=db_session)

    assert callback_resp.headers["location"].startswith("https://athenaindex.app/auth/callback?")


async def test_callback_rejects_unknown_state(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await callback(code="fake-code", state="never-issued", session=db_session)
    assert exc_info.value.status_code == 400


async def test_callback_state_is_single_use(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "post", _fake_token_exchange(orcid="0000-0000-0000-0003")
    )

    login_resp = await login(origin="https://pr-9.athenaindex.app")
    state = login_resp.headers["location"].split("state=")[1].split("&")[0]

    await callback(code="fake-code", state=state, session=db_session)
    with pytest.raises(HTTPException) as exc_info:
        await callback(code="fake-code", state=state, session=db_session)
    assert exc_info.value.status_code == 400
