"""Environment-backed application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_OWNER_USER_ID = "00000000-0000-4000-8000-000000000001"
LOCAL_OWNER_NAMESPACE = UUID(LOCAL_OWNER_USER_ID)


class Settings(BaseSettings):
    """Helmet runtime settings.

    Optional external-service values allow health endpoints and tests to start.
    Call the corresponding ``require_*`` method before using a service.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HELMET_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    sleeper_base_url: AnyHttpUrl = AnyHttpUrl("https://api.sleeper.app/v1")
    sleeper_requests_per_minute: int = Field(default=900, ge=1, le=1000)
    persistence_backend: Literal["local", "supabase"] = "local"
    local_database_path: Path = Path("data/helmet.db")
    supabase_url: AnyHttpUrl | None = None
    supabase_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    owner_user_id: str | None = None

    @field_validator(
        "supabase_url",
        "supabase_key",
        "anthropic_api_key",
        "owner_user_id",
        mode="before",
    )
    @classmethod
    def blank_means_unset(cls, value: Any) -> Any:
        """Treat an empty environment variable as an absent one."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def production_requires_supabase(self) -> Settings:
        if self.environment == "production" and self.persistence_backend == "local":
            raise ValueError(
                "the local SQLite backend is for development only; "
                "set HELMET_PERSISTENCE_BACKEND=supabase in production"
            )
        return self

    def require_supabase(self) -> tuple[str, str, str]:
        if self.supabase_url is None or self.supabase_key is None or self.owner_user_id is None:
            raise RuntimeError(
                "Supabase is not configured; set HELMET_SUPABASE_URL, "
                "HELMET_SUPABASE_KEY, and HELMET_OWNER_USER_ID"
            )
        return (
            str(self.supabase_url).rstrip("/"),
            self.supabase_key.get_secret_value(),
            self.resolve_owner_user_id(),
        )

    def resolve_owner_user_id(self) -> str:
        """Return the owning user as a UUID.

        Supabase rows must reference a real ``auth.users`` UUID. The local
        backend has no auth table, so it accepts any label and maps it to a
        stable UUID instead.
        """
        if self.owner_user_id is None:
            if self.persistence_backend == "local":
                return LOCAL_OWNER_USER_ID
            raise RuntimeError("owner is not configured; set HELMET_OWNER_USER_ID")
        try:
            return str(UUID(self.owner_user_id))
        except ValueError:
            if self.persistence_backend == "local":
                return str(uuid5(LOCAL_OWNER_NAMESPACE, self.owner_user_id))
            raise RuntimeError(
                "HELMET_OWNER_USER_ID must be the Supabase auth user UUID, "
                f"not {self.owner_user_id!r}"
            ) from None

    def require_anthropic_key(self) -> str:
        if self.anthropic_api_key is None:
            raise RuntimeError("Anthropic is not configured; set HELMET_ANTHROPIC_API_KEY")
        return self.anthropic_api_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
