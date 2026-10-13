from __future__ import annotations

import pytest

from personal_agent.settings import SettingsError, load_settings, load_worker_settings


def test_production_is_the_default_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://agent:secret@db:5432/agent")

    settings = load_settings()

    assert settings.app_env == "production"


def test_missing_required_setting_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SettingsError, match="DATABASE_URL"):
        load_settings()


def test_invalid_value_is_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "not-a-url-with-secret-token-123")

    with pytest.raises(SettingsError) as raised:
        load_settings()

    assert "secret-token-123" not in str(raised.value)
    assert "DATABASE_URL" in str(raised.value)


def test_pool_bounds_must_be_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://agent:secret@db:5432/agent")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "6")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "5")

    with pytest.raises(SettingsError, match="DB_POOL_MIN_SIZE"):
        load_settings()


_WORKER_ENV = {
    "DATABASE_URL": "postgresql://agent:secret@db:5432/agent",
    "TELEGRAM_BOT_TOKEN": "123:bot-token-value",
    "TELEGRAM_ALLOWED_USER_IDS": "1001, 1002",
    "GATEWAY_URL": "http://127.0.0.1:8360",
    "GATEWAY_API_KEY": "lgw.dev.abcdefghijkl.gateway-key-value",
}


def _set_worker_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for name, value in {**_WORKER_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_worker_settings_parse_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_worker_env(monkeypatch)

    settings = load_worker_settings()

    assert settings.telegram_allowed_user_ids == frozenset({1001, 1002})
    assert settings.reply_profile == "reply"
    assert settings.default_timezone == "Asia/Jakarta"


def test_missing_worker_secrets_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_worker_env(monkeypatch, TELEGRAM_BOT_TOKEN=None, GATEWAY_API_KEY=None)

    with pytest.raises(SettingsError) as raised:
        load_worker_settings()

    assert "TELEGRAM_BOT_TOKEN" in str(raised.value)
    assert "GATEWAY_API_KEY" in str(raised.value)


def test_worker_secrets_are_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_worker_env(monkeypatch, TELEGRAM_ALLOWED_USER_IDS="not-a-number", GATEWAY_URL="nope")

    with pytest.raises(SettingsError) as raised:
        load_worker_settings()

    message = str(raised.value)
    assert "TELEGRAM_ALLOWED_USER_IDS" in message
    assert "GATEWAY_URL" in message
    for value in ("bot-token-value", "gateway-key-value", "not-a-number"):
        assert value not in message


def test_group_ids_cannot_be_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_worker_env(monkeypatch, TELEGRAM_ALLOWED_USER_IDS="-100123")

    with pytest.raises(SettingsError, match="TELEGRAM_ALLOWED_USER_IDS"):
        load_worker_settings()


def test_unknown_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_worker_env(monkeypatch, DEFAULT_TIMEZONE="Mars/Olympus")

    with pytest.raises(SettingsError, match="DEFAULT_TIMEZONE"):
        load_worker_settings()


def test_migrate_settings_do_not_need_worker_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_worker_env(monkeypatch, TELEGRAM_BOT_TOKEN=None, GATEWAY_API_KEY=None)

    assert load_settings().database_url is not None
