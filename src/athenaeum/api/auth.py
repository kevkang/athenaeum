import secrets
from urllib.parse import urlencode, quote

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from athenaeum.api.deps import SessionDep
from athenaeum.config import get_settings
from athenaeum.security import issue_session_token
from athenaeum.storage.models import User

router = APIRouter(prefix="/auth/orcid", tags=["auth"])

_state_store: set[str] = set()


@router.get("/login")
async def login() -> RedirectResponse:
    settings = get_settings()
    if not settings.orcid_client_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "ORCID OAuth not configured"
        )
    state = secrets.token_urlsafe(32)
    _state_store.add(state)
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
    _state_store.discard(state)

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
    return RedirectResponse(f"{settings.frontend_url}/auth/callback?{params}")
