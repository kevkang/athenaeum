import pytest
from cryptography.fernet import Fernet

from athenaeum import crypto
from athenaeum.config import get_settings


@pytest.fixture(autouse=True)
def _clear_caches():
    get_settings.cache_clear()
    crypto._fernet.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


def test_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    ciphertext = crypto.encrypt_secret("my-zotero-api-key")
    assert ciphertext != "my-zotero-api-key"
    assert crypto.decrypt_secret(ciphertext) == "my-zotero-api-key"


def test_encrypt_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()

    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.encrypt_secret("x")


def test_encrypt_rejects_malformed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    get_settings.cache_clear()

    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.encrypt_secret("x")


def test_decrypt_rejects_bad_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.decrypt_secret("not-valid-ciphertext")
