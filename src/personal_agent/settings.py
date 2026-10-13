"""Typed process settings, validated once at startup."""

from __future__ import annotations

from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, HttpUrl, PostgresDsn, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class SettingsError(RuntimeError):
    """Invalid configuration. The message names variables, never their values."""


class Settings(BaseSettings):
    """Settings every process needs, including the migrate step."""

    # Field names map to upper-case environment variables (APP_ENV, DATABASE_URL, ...).
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", frozen=True)

    # Production is the safe default; development behaviour is opt-in.
    app_env: Literal["development", "production"] = "production"
    app_revision: str = Field(default="unknown", max_length=64)
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    database_url: PostgresDsn
    db_pool_min_size: int = Field(default=1, ge=1, le=20)
    db_pool_max_size: int = Field(default=5, ge=1, le=50)


class WorkerSettings(Settings):
    """Settings of the long-running worker: Telegram, the gateway, and turn pacing."""

    telegram_bot_token: SecretStr = Field(min_length=1)
    # Comma-separated Telegram user IDs; only their private chats are answered.
    telegram_allowed_user_ids: Annotated[frozenset[int], NoDecode] = Field(min_length=1)
    gateway_url: HttpUrl
    gateway_api_key: SecretStr = Field(min_length=1)
    # The gateway contract profile that maps reply calls to a model and settings.
    reply_profile: str = Field(default="reply", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    # IANA timezone used until the person's profile sets one.
    default_timezone: str = "Asia/Jakarta"
    turn_debounce_seconds: float = Field(default=2.0, ge=0, le=30)
    recent_window_tokens: int = Field(default=6_000, ge=500, le=100_000)
    offline_notice_after_seconds: int = Field(default=300, ge=30)

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(part) for part in value.split(",") if part.strip())
        return value

    @field_validator("telegram_allowed_user_ids")
    @classmethod
    def _positive_user_ids(cls, value: frozenset[int]) -> frozenset[int]:
        # Negative IDs are groups and channels, which are never answered.
        if any(user_id <= 0 for user_id in value):
            raise ValueError("user IDs must be positive")
        return value

    @field_validator("default_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown IANA timezone") from None
        return value


def _load[T: Settings](settings_class: type[T]) -> T:
    try:
        settings = settings_class()  # pyright: ignore[reportCallIssue]  # values come from the environment
    except ValidationError as exc:
        names = sorted({str(error["loc"][0]).upper() for error in exc.errors() if error["loc"]})
        raise SettingsError(f"Invalid or missing settings: {', '.join(names)}") from None
    if settings.db_pool_min_size > settings.db_pool_max_size:
        raise SettingsError("Invalid settings: DB_POOL_MIN_SIZE exceeds DB_POOL_MAX_SIZE")
    return settings


def load_settings() -> Settings:
    """Load settings from the environment or stop with the names of invalid variables."""
    return _load(Settings)


def load_worker_settings() -> WorkerSettings:
    """Load the worker's settings or stop with the names of invalid variables."""
    return _load(WorkerSettings)
