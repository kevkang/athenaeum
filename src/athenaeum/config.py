from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file so the path is correct regardless of CWD.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"

# Shared with main.py's startup guard, which refuses to boot with this value
# outside of ENVIRONMENT=development — see that check for why.
DEFAULT_JWT_SECRET = "dev-secret-change-me"

# Every PR preview shares the single production API (see
# .github/workflows/preview.yml) at pr-N.athenaindex.app, so a fixed
# frontend_url isn't enough — CORS (main.py) and the ORCID redirect origin
# (api/auth.py) both need to recognize any preview subdomain, not just
# production, without opening up to arbitrary origins.
ALLOWED_FRONTEND_ORIGIN_PATTERN = r"^https://(pr-\d+\.)?athenaindex\.app$|^http://localhost:\d+$"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    # "development" (the default) is what a bare `pip install -e .` run or
    # the dev docker-compose.yml get implicitly. Real deployments must set
    # ENVIRONMENT=production explicitly (docker-compose.prod.yml does) — see
    # main.py's startup guard, which uses this to reject the default
    # jwt_secret instead of silently booting with a publicly-known key.
    environment: str = "development"

    database_url: str = (
        "postgresql+asyncpg://athenaeum:athenaeum@localhost:5432/athenaeum"
    )
    neo4j_url: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "athenaeum"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = DEFAULT_JWT_SECRET
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
    # Deliberately a different model line from synthesis_model, not just a
    # different size within the same one — PLAN.md's own design intent is
    # that extraction and its independent check shouldn't share training-time
    # blind spots. Also deliberately NOT the same lightweight tier used for
    # summarize_model/zotero_find_model below: this is the credibility-
    # critical path, not a place to economize.
    checker_model: str = "claude-sonnet-5"
    # An audit fails rather than reports when more than this share of its
    # evidence checks come back unusable (synthesize/audit.py). A skipped
    # check leaves a citation recorded as unsupported without anything having
    # judged it, so past some rate the findings accuse an author's citations
    # on the strength of a broken response. 5% is a starting point, not a
    # measured value: nothing yet says what rate is normal for this model.
    audit_malformed_threshold: float = 0.05
    summarize_model: str = "claude-haiku-4-5-20251001"
    zotero_find_model: str = "claude-haiku-4-5-20251001"

    s3_bucket: str | None = None
    aws_region: str = "us-east-1"
    s3_endpoint_url: str | None = None  # set only for local MinIO; unset hits real AWS
    # Presigned figure URLs are handed to a browser, not called server-side —
    # in local dev s3_endpoint_url is the docker-compose-internal
    # `http://minio:9000`, which a browser outside that network can't
    # resolve. This is the browser-reachable equivalent (e.g.
    # `http://localhost:9000`); unset in production, where s3_endpoint_url
    # is also unset and both cases hit real, already-public AWS.
    s3_public_endpoint_url: str | None = None

    secret_encryption_key: str | None = None
    zotero_api_base: str = "https://api.zotero.org"

    # Path (inside the api/worker containers) where the operator's *entire*
    # home directory is bind-mounted read-only — see docker-compose.yml.
    # Only meaningful for a single-operator local deployment — there's no
    # sense in which a browser-facing multi-user server could resolve "the
    # user's local Zotero install" otherwise. Which *subfolder* under this
    # mount is the actual Zotero data directory is a runtime choice made from
    # the UI (persisted in the zotero_local_config table), not a static env
    # var — that mount itself is the only part that needs a container restart.
    zotero_local_host_mount: str | None = "/host-home"


@lru_cache
def get_settings() -> Settings:
    return Settings()
