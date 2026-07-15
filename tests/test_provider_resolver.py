import uuid
from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet

from athenaeum import crypto
from athenaeum.config import Settings, get_settings
from athenaeum.provider.resolver import NoLLMKeyConfigured, resolve_api_key


@dataclass
class _FakeCredentialsRow:
    api_key_encrypted: str


class _FakeSession:
    """Minimal stand-in for AsyncSession.get(LLMCredentials, user_id)."""

    def __init__(self, row: _FakeCredentialsRow | None) -> None:
        self._row = row

    async def get(self, model: type, pk: uuid.UUID) -> _FakeCredentialsRow | None:
        return self._row


@pytest.fixture(autouse=True)
def _clear_caches():
    get_settings.cache_clear()
    crypto._fernet.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


async def test_per_user_key_wins_over_server_key() -> None:
    encrypted = crypto.encrypt_secret("user-owned-key")
    session = _FakeSession(_FakeCredentialsRow(api_key_encrypted=encrypted))
    settings = Settings(anthropic_api_key="server-key")

    resolved = await resolve_api_key(session, uuid.uuid4(), settings)

    assert resolved == "user-owned-key"


async def test_falls_back_to_server_key_when_no_user_row() -> None:
    session = _FakeSession(None)
    settings = Settings(anthropic_api_key="server-key")

    resolved = await resolve_api_key(session, uuid.uuid4(), settings)

    assert resolved == "server-key"


async def test_raises_when_neither_key_configured() -> None:
    session = _FakeSession(None)
    settings = Settings(anthropic_api_key=None)

    with pytest.raises(NoLLMKeyConfigured):
        await resolve_api_key(session, uuid.uuid4(), settings)
