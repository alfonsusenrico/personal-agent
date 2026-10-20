from __future__ import annotations

from pathlib import Path

import pytest

from personal_agent.backup.service import BackupSettings
from personal_agent.settings import SettingsError, load, load_settings, load_worker_settings


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


def _secret(tmp_path: Path, name: str, value: str, mode: int = 0o640) -> str:
    path = tmp_path / name
    path.write_text(value + "\n")
    path.chmod(mode)
    return str(path)


def _set_file_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_worker_env(
        monkeypatch,
        DATABASE_URL=None,
        TELEGRAM_BOT_TOKEN=None,
        GATEWAY_API_KEY=None,
        DATABASE_HOST="db",
        DATABASE_NAME="personal_agent",
        DATABASE_USER="personal_agent",
    )
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", _secret(tmp_path, "password", "p@ss/w:rd"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", _secret(tmp_path, "token", "123:file-token"))
    monkeypatch.setenv("GATEWAY_API_KEY_FILE", _secret(tmp_path, "key", "lgw_prod_file_key"))


def test_secrets_and_the_database_url_come_from_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_file_env(monkeypatch, tmp_path)

    settings = load_worker_settings()

    assert settings.telegram_bot_token.get_secret_value() == "123:file-token"
    assert settings.gateway_api_key.get_secret_value() == "lgw_prod_file_key"
    assert str(settings.database_url) == (
        "postgresql://personal_agent:p%40ss%2Fw%3Ard@db:5432/personal_agent"
    )


def test_an_owner_only_secret_file_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GATEWAY_API_KEY_FILE", _secret(tmp_path, "key600", "k" * 20, 0o600))

    assert load_worker_settings().gateway_api_key.get_secret_value() == "k" * 20


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o604])
def test_a_secret_file_open_to_others_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: int
) -> None:
    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", _secret(tmp_path, "open", "123:open", mode))

    with pytest.raises(SettingsError) as raised:
        load_worker_settings()

    assert "TELEGRAM_BOT_TOKEN_FILE" in str(raised.value)
    assert "123:open" not in str(raised.value)


def test_a_secret_and_its_file_together_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.setenv("GATEWAY_API_KEY", "lgw_inline_value")

    with pytest.raises(SettingsError, match="GATEWAY_API_KEY or GATEWAY_API_KEY_FILE"):
        load_worker_settings()


@pytest.mark.parametrize("content", ["", "\n"])
def test_a_missing_or_empty_secret_file_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    _set_file_env(monkeypatch, tmp_path)
    empty = tmp_path / "empty"
    empty.write_text(content)
    empty.chmod(0o640)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(empty))
    with pytest.raises(SettingsError, match="TELEGRAM_BOT_TOKEN_FILE"):
        load_worker_settings()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_FILE", str(tmp_path / "absent"))
    with pytest.raises(SettingsError, match="TELEGRAM_BOT_TOKEN_FILE cannot be read"):
        load_worker_settings()


def test_database_parts_are_named_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.delenv("DATABASE_HOST")

    with pytest.raises(SettingsError, match="DATABASE_HOST"):
        load_settings()

    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://agent:secret@db:5432/agent")
    with pytest.raises(SettingsError, match="DATABASE_URL or DATABASE_PASSWORD_FILE"):
        load_settings()


def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The production Compose overlay clears the local DATABASE_URL default this way.
    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REPLY_PROFILE", "")

    settings = load_worker_settings()

    assert settings.database_url.hosts()[0]["host"] == "db"
    assert settings.reply_profile == "reply"


def test_the_backup_password_comes_from_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_file_env(monkeypatch, tmp_path)
    monkeypatch.delenv("RESTIC_PASSWORD", raising=False)
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", _secret(tmp_path, "restic", "r" * 32))

    settings = load(BackupSettings)

    assert settings.restic_password.get_secret_value() == "r" * 32
