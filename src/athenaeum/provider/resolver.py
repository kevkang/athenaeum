"""Resolve which Anthropic API key/provider to use for a given user's request.

A per-user LLMCredentials row (see storage/models.py) always takes precedence
over the server's global settings.anthropic_api_key — a user who has connected
their own key should never fall back to the server's key while theirs is set.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from athenaeum.config import Settings
from athenaeum.crypto import decrypt_secret
from athenaeum.provider.anthropic_client import AnthropicProvider
from athenaeum.storage.models import LLMCredentials


class NoLLMKeyConfigured(RuntimeError):
    """Neither a per-user key nor a server-wide key is available."""


async def resolve_api_key(session: AsyncSession, user_id: uuid.UUID, settings: Settings) -> str:
    row = await session.get(LLMCredentials, user_id)
    if row is not None:
        return decrypt_secret(row.api_key_encrypted)
    if settings.anthropic_api_key:
        return settings.anthropic_api_key
    raise NoLLMKeyConfigured("No Anthropic API key configured for this user or server")


async def get_provider_for_user(
    session: AsyncSession, user_id: uuid.UUID, model: str, settings: Settings
) -> AnthropicProvider:
    api_key = await resolve_api_key(session, user_id, settings)
    return AnthropicProvider(api_key, model)
