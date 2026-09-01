"""Centralised, validated application configuration.

All configuration enters the application here and nowhere else. Modules import
``get_settings()`` rather than reading ``os.environ`` directly, so that every
setting has one declared type, one default, and one place to audit.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
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

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.production


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
