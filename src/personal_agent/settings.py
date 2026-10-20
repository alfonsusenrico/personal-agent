"""Typed process settings, validated once at startup.

A deployment supplies secrets as files (`<NAME>_FILE`, the deployment standard's
contract): Compose mounts them under /run/secrets, so values stay out of env files. The
database URL is then built from DATABASE_HOST, DATABASE_PORT, DATABASE_NAME,
DATABASE_USER, and DATABASE_PASSWORD_FILE, so no URL with a password is ever written.
"""

from __future__ import annotations

import os
import stat
from typing import Annotated, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, HttpUrl, PostgresDsn, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class SettingsError(RuntimeError):
    """Invalid configuration. The message names variables, never their values."""


SECRET_FILE_SETTINGS = ("TELEGRAM_BOT_TOKEN", "GATEWAY_API_KEY", "RESTIC_PASSWORD")
DATABASE_PARTS = ("DATABASE_HOST", "DATABASE_NAME", "DATABASE_USER")
MAX_SECRET_FILE_BYTES = 4096


class Settings(BaseSettings):
    """Settings every process needs, including the migrate step."""

    # Field names map to upper-case environment variables (APP_ENV, DATABASE_URL, ...).
    # Empty variables count as unset, so a deployment can clear a Compose default.
    model_config = SettingsConfigDict(
        case_sensitive=False, extra="ignore", frozen=True, env_ignore_empty=True
    )

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
    # Least time between streamed draft updates on Telegram.
    draft_interval_seconds: float = Field(default=1.0, ge=0.3, le=10)
    # The Bot API base URL; only rehearsals point it at a fake Bot API.
    telegram_api_url: HttpUrl = HttpUrl("https://api.telegram.org")
    # The health endpoint. The image listens on every interface inside the container and
    # Compose publishes the port on loopback only.
    health_host: str = "127.0.0.1"
    health_port: int = Field(default=8080, ge=1, le=65535)

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


def read_secret_file(name: str, path_text: str) -> str:
    """The content of a `<NAME>_FILE` secret; errors name the variable, never the value.

    Deployment secrets are 0640 (the deploy user and its group, which the container
    joins); a file writable by its group or open to others is refused."""
    try:
        descriptor = os.open(path_text, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise SettingsError(f"Invalid settings: {name}_FILE cannot be read") from None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o037:
            raise SettingsError(
                f"Invalid settings: {name}_FILE must be a regular file closed to others"
            )
        data = stream.read(MAX_SECRET_FILE_BYTES + 1)
    try:
        value = data.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        raise SettingsError(f"Invalid settings: {name}_FILE is not UTF-8 text") from None
    if not value or len(data) > MAX_SECRET_FILE_BYTES:
        raise SettingsError(f"Invalid settings: {name}_FILE is empty or too large")
    return value


def _file_sources() -> dict[str, str]:
    """Settings supplied through files, as keyword overrides for a settings class."""
    environment = {key.upper(): value for key, value in os.environ.items() if value}
    overrides: dict[str, str] = {}
    for name in SECRET_FILE_SETTINGS:
        path_text = environment.get(f"{name}_FILE")
        if path_text is None:
            continue
        if name in environment:
            raise SettingsError(f"Invalid settings: set {name} or {name}_FILE, not both")
        overrides[name.lower()] = read_secret_file(name, path_text)
    password_file = environment.get("DATABASE_PASSWORD_FILE")
    if password_file is not None:
        if "DATABASE_URL" in environment:
            raise SettingsError(
                "Invalid settings: set DATABASE_URL or DATABASE_PASSWORD_FILE, not both"
            )
        missing = [name for name in DATABASE_PARTS if name not in environment]
        if missing:
            raise SettingsError(f"Invalid or missing settings: {', '.join(missing)}")
        password = quote(read_secret_file("DATABASE_PASSWORD", password_file), safe="")
        user = quote(environment["DATABASE_USER"], safe="")
        port = environment.get("DATABASE_PORT", "5432")
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise SettingsError("Invalid settings: DATABASE_PORT must be a port number")
        overrides["database_url"] = (
            f"postgresql://{user}:{password}@{environment['DATABASE_HOST']}:{port}/"
            f"{quote(environment['DATABASE_NAME'], safe='')}"
        )
    return overrides


def load[T: Settings](settings_class: type[T]) -> T:
    """Load a settings class from the environment and secret files, or stop with the names
    of invalid variables."""
    overrides = _file_sources()
    try:
        settings = settings_class(**overrides)  # pyright: ignore[reportArgumentType]  # validated by pydantic
    except ValidationError as exc:
        names = sorted({str(error["loc"][0]).upper() for error in exc.errors() if error["loc"]})
        raise SettingsError(f"Invalid or missing settings: {', '.join(names)}") from None
    if settings.db_pool_min_size > settings.db_pool_max_size:
        raise SettingsError("Invalid settings: DB_POOL_MIN_SIZE exceeds DB_POOL_MAX_SIZE")
    return settings


def load_settings() -> Settings:
    """Load settings from the environment or stop with the names of invalid variables."""
    return load(Settings)


def load_worker_settings() -> WorkerSettings:
    """Load the worker's settings or stop with the names of invalid variables."""
    return load(WorkerSettings)
