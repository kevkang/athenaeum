"""At-rest encryption for per-user third-party secrets (e.g. Zotero API keys).

Separate from security.py, which handles JWT session tokens (signing, not encryption).
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from athenaeum.config import get_settings


class EncryptionNotConfigured(RuntimeError):
    """Raised when SECRET_ENCRYPTION_KEY is unset or malformed."""


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.secret_encryption_key:
        raise EncryptionNotConfigured("SECRET_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(settings.secret_encryption_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise EncryptionNotConfigured("SECRET_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionNotConfigured("stored secret could not be decrypted") from exc
