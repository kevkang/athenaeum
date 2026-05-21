from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file so the path is correct regardless of CWD.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = (
        "postgresql+asyncpg://athenaeum:athenaeum@localhost:5432/athenaeum"
    )
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "athenaeum"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiry_seconds: int = 60 * 60 * 24 * 7

    orcid_client_id: str | None = None
    orcid_client_secret: str | None = None
    orcid_redirect_uri: str = "http://localhost:8000/auth/orcid/callback"
    orcid_api_base: str = "https://sandbox.orcid.org"
    frontend_url: str = "http://localhost:5173"

    grobid_url: str = "http://localhost:8070"
    unpaywall_email: str = "contact@athenaeum.app"
    embed_model: str = "BAAI/bge-base-en-v1.5"

    anthropic_api_key: str | None = None
    synthesis_model: str = "claude-opus-4-7"
    checker_model: str = "claude-opus-4-7"

    s3_bucket: str | None = None
    aws_region: str = "us-east-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
