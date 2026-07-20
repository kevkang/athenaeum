"""Per-user "bring your own" LLM API key management (Claude, for now)."""
from __future__ import annotations

from datetime import datetime, timezone

import anthropic
import structlog
from fastapi import APIRouter, HTTPException

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.config import get_settings
from athenaeum.crypto import EncryptionNotConfigured, encrypt_secret
from athenaeum.domain.models import LLMCredentialsIn, LLMCredentialsStatus
from athenaeum.provider.anthropic_client import validate_api_key
from athenaeum.storage.models import LLMCredentials

log = structlog.get_logger()
router = APIRouter(prefix="/llm", tags=["llm-credentials"])


@router.get("/credentials", response_model=LLMCredentialsStatus)
async def get_credentials_status(
    session: SessionDep,
    user: CurrentUserDep,
) -> LLMCredentialsStatus:
    row = await session.get(LLMCredentials, user.id)
    if row is None:
        return LLMCredentialsStatus(connected=False)
    return LLMCredentialsStatus(connected=True, provider=row.provider)


@router.put("/credentials", response_model=LLMCredentialsStatus)
async def save_credentials(
    payload: LLMCredentialsIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> LLMCredentialsStatus:
    settings = get_settings()
    if not settings.secret_encryption_key:
        raise HTTPException(
            status_code=501, detail="LLM key storage not configured (set SECRET_ENCRYPTION_KEY)"
        )

    try:
        await validate_api_key(payload.api_key)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        log.warning("llm.validate_failed", kind="auth", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid Claude API key") from exc
    except anthropic.RateLimitError as exc:
        log.warning("llm.validate_failed", kind="rate_limit", error=str(exc))
        raise HTTPException(
            status_code=429, detail="Anthropic is rate-limiting this key — try again shortly"
        ) from exc
    except Exception as exc:
        log.warning("llm.validate_failed", kind="unknown", error=str(exc))
        raise HTTPException(
            status_code=502, detail="Could not reach Anthropic to validate credentials"
        ) from exc

    try:
        encrypted = encrypt_secret(payload.api_key)
    except EncryptionNotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    row = await session.get(LLMCredentials, user.id)
    if row is None:
        row = LLMCredentials(user_id=user.id, provider="anthropic", api_key_encrypted=encrypted)
        session.add(row)
    else:
        row.api_key_encrypted = encrypted
        row.updated_at = datetime.now(timezone.utc)

    await session.commit()
    return LLMCredentialsStatus(connected=True, provider="anthropic")


@router.delete("/credentials", status_code=204)
async def delete_credentials(
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    row = await session.get(LLMCredentials, user.id)
    if row is not None:
        await session.delete(row)
        await session.commit()
