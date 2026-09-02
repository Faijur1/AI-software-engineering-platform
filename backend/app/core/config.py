"""Centralised, validated application configuration.

All configuration enters the application here and nowhere else. Modules import
``get_settings()`` rather than reading ``os.environ`` directly, so that every
setting has one declared type, one default, and one place to audit.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    development = "development"
    test = "test"
    production = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The repository root .env is shared with the frontend so that a single
        # file configures the whole stack in development.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = AppEnv.development
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "console"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+psycopg://app:app@localhost:5432/aisep")
    )
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    # Number of connections held open per process. Workers and the API server
    # each get their own pool, so this is deliberately modest.
    db_pool_size: int = 5
    db_pool_max_overflow: int = 5

    # ---------- GitHub OAuth (milestone 2) ----------
    # Empty by default so the application still starts without credentials; the
    # auth routes fail loudly with a configuration error instead, which keeps a
    # missing secret from being mistaken for a broken sign-in.
    github_client_id: str = ""
    github_client_secret: SecretStr = SecretStr("")
    # Read-only scopes only. Stage 1 performs no GitHub writes (docs/security.md).
    github_scopes: str = "read:user user:email repo"

    # ---------- Session / secrets ----------
    # Signs the session cookie. Named NEXTAUTH_SECRET in .env for continuity
    # with the original design; see ADR-009 for why the backend now owns OAuth.
    session_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("SESSION_SECRET", "NEXTAUTH_SECRET")
    )
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_cookie_name: str = "aisep_session"
    # Fernet key used to encrypt GitHub access tokens at rest.
    token_encryption_key: SecretStr = SecretStr("")

    # ---------- LLM / embeddings (milestone 4) ----------
    ollama_base_url: str = "http://localhost:11434"
    # Generation and embedding models are configured independently on purpose:
    # they are swapped for different reasons and at different times.
    llm_model: str = "qwen2.5-coder:7b"
    embedding_model: str = "nomic-embed-text"
    # Must match the model's real output size. The database column is fixed at
    # this width, so a mismatch is a hard failure rather than a silent
    # truncation -- see app/llm/ollama.py.
    embedding_dimensions: int = 768
    # Generous: the first request to Ollama loads the model, which took ~23s
    # for nomic-embed-text on a cold start.
    llm_timeout_seconds: int = 120
    # Chunks per embedding request. Larger batches amortise the round trip;
    # too large and one failure wastes more work and the request can time out.
    embedding_batch_size: int = 32

    # ---------- Ingestion ----------
    # Extra directory names excluded from indexing, comma-separated. Empty by
    # default: these are deployment-specific, and a name that is benchmark
    # scaffolding in one repository is ordinary source in another.
    #
    # Set to "eval" when running this project's own agent benchmark against
    # this repository, or the agent retrieves the benchmark instead of the code
    # the benchmark asks about.
    extra_excluded_directories_raw: str = Field(
        default="", alias="EXTRA_EXCLUDED_DIRECTORIES"
    )

    # ---------- URLs ----------
    # Where the browser is sent after a completed OAuth round trip, and the
    # origin allowed to make credentialed cross-site calls to this API.
    frontend_url: str = "http://localhost:3000"
    # Public base URL of this API. The GitHub callback URL is derived from it,
    # so it must match the OAuth App registration exactly.
    backend_url: str = "http://localhost:8000"

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.production

    @property
    def github_callback_url(self) -> str:
        return f"{self.backend_url.rstrip('/')}/auth/github/callback"

    @property
    def extra_excluded_directories(self) -> frozenset[str]:
        parts = self.extra_excluded_directories_raw.split(",")
        return frozenset(part.strip() for part in parts if part.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
