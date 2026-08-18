import re
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from athenaeum.api.deps import SessionDep
from athenaeum.config import ALLOWED_FRONTEND_ORIGIN_PATTERN, get_settings
from athenaeum.security import issue_session_token
from athenaeum.storage.models import User

router = APIRouter(prefix="/auth/orcid", tags=["auth"])

# ORCID's own redirect_uri (backend callback) must stay fixed — it's
# pre-registered with ORCID and can't vary per-request. But every PR preview
# shares the single production API (see .github/workflows/preview.yml), so
# without this, the *frontend* redirect at the end of login always landed on
# settings.frontend_url regardless of which frontend actually started the
# flow — previews could never complete login, always bouncing back to
# production. Only production, pr-N previews, and local dev ever legitimately
# initiate login, so validating against this pattern before ever trusting an
# origin closes the obvious open-redirect risk of just honoring whatever the
# query string says.
_ALLOWED_FRONTEND_ORIGIN_RE = re.compile(ALLOWED_FRONTEND_ORIGIN_PATTERN)

# state -> the frontend origin to send the user back to, validated at
# /login time so an attacker can't inject an arbitrary redirect target by
# forging a state value — state itself is an unguessable token ORCID round-
# trips back exactly, so this binding can't be tampered with in between.
_state_store: dict[str, str] = {}


@router.get("/login")
async def login(origin: str | None = Query(default=None)) -> RedirectResponse:
    settings = get_settings()
    if not settings.orcid_client_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "ORCID OAuth not configured"
        )
    resolved_origin = (
        origin if origin and _ALLOWED_FRONTEND_ORIGIN_RE.match(origin) else settings.frontend_url
    )
    state = secrets.token_urlsafe(32)
    _state_store[state] = resolved_origin
    params = {
        "client_id": settings.orcid_client_id,
        "response_type": "code",
        "scope": "/authenticate",
        "redirect_uri": settings.orcid_redirect_uri,
        "state": state,
    }
    return RedirectResponse(f"{settings.orcid_api_base}/oauth/authorize?{urlencode(params)}")


@router.get("/callback")
async def callback(code: str, state: str, session: SessionDep) -> RedirectResponse:
    settings = get_settings()
    if state not in _state_store:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid state")
    frontend_origin = _state_store.pop(state)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.orcid_api_base}/oauth/token",
            data={
                "client_id": settings.orcid_client_id,
                "client_secret": settings.orcid_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.orcid_redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, f"ORCID token exchange failed: {resp.text}"
        )
    data = resp.json()
    orcid = data["orcid"]
    display_name = data.get("name")

    result = await session.execute(select(User).where(User.orcid == orcid))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(orcid=orcid, display_name=display_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = issue_session_token(user.id)
    params = urlencode({
        "token": token,
        "orcid": orcid,
        "name": display_name or "",
        "user_id": str(user.id),
    })
    return RedirectResponse(f"{frontend_origin}/auth/callback?{params}")
