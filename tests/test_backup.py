"""Backups with a fake restic: when one is due, the commands it runs, and failures."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from personal_agent.backup.restic import Restic, ResticError
from personal_agent.backup.restore_check import DatabaseState, compare
from personal_agent.backup.service import BackupSettings, is_due, run_forever, run_once
from personal_agent.feedback.review import backup_age, render
from personal_agent.feedback.store import Counts, OpenFlag

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
DATABASE_URL = "postgresql://agent:db-secret@db:5432/personal_agent"
PASSWORD = "restic-secret-value"


class FakeRestic:
    """Answers restic commands from in-memory state and records each call."""

    def __init__(self, *, snapshots: list[datetime] | None = None, exists: bool = True) -> None:
        self.exists = exists
        self.snapshots = list(snapshots or [])
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.backup_exit = 0

    def __call__(
        self, args: list[str], env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((args, dict(env)))
        command = args[2]
        stdout = b""
        code = 0
        if command in ("cat", "snapshots") and not self.exists:
            code = 10
        elif command == "init":
            self.exists = True
        elif command == "snapshots":
            newest = sorted(self.snapshots)[-1:]
            stdout = json.dumps(
                [
                    {"time": moment.isoformat().replace("+00:00", ".123456789+00:00")}
                    for moment in newest
                ]
            ).encode()
        elif command == "backup":
            code = self.backup_exit
            if code == 0:
                self.snapshots.append(NOW)
                stdout = (
                    b'{"message_type":"status"}\n'
                    + json.dumps(
                        {"message_type": "summary", "snapshot_id": "abcdef1234", "data_added": 2048}
                    ).encode()
                )
        return subprocess.CompletedProcess(args, code, stdout, b"")

    @property
    def commands(self) -> list[str]:
        return [" ".join(args[2:4]) if args[2] == "cat" else args[2] for args, _ in self.calls]


def _restic(fake: FakeRestic) -> Restic:
    return Restic("/backups", PASSWORD, runner=fake)


def test_a_backup_is_due_when_none_exists_or_the_newest_is_a_day_old() -> None:
    assert is_due(None, NOW)
    assert is_due(NOW - timedelta(hours=30), NOW)
    assert is_due(NOW - timedelta(hours=24), NOW)
    assert not is_due(NOW - timedelta(hours=3), NOW)


def test_first_run_creates_the_repository_and_backs_up(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    fake = FakeRestic(exists=False)

    assert run_once(_restic(fake), DATABASE_URL, NOW)

    assert fake.commands == [
        "cat config",
        "init",
        "unlock",
        "snapshots",
        "backup",
        "forget",
        "check",
    ]
    backup_args, backup_env = fake.calls[4]
    assert backup_args[-4:] == ["--stdin-from-command", "--", "pg_dump", "--format=custom"]
    assert backup_args[4:6] == ["--host", "personal-agent"]
    assert backup_env["PGPASSWORD"] == "db-secret"
    assert (backup_env["PGHOST"], backup_env["PGDATABASE"]) == ("db", "personal_agent")
    assert backup_env["RESTIC_PASSWORD"] == PASSWORD
    assert all("--no-cache" in args for args, _ in fake.calls)
    forget_args = fake.calls[5][0]
    assert forget_args[3:] == [
        "--host",
        "personal-agent",
        "--keep-daily",
        "7",
        "--keep-weekly",
        "4",
        "--prune",
    ]
    assert fake.calls[6][0][3:] == ["--read-data"]
    for args, _ in fake.calls:
        assert not any(secret in " ".join(args) for secret in (PASSWORD, "db-secret"))
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "abcdef12" in logged and PASSWORD not in logged and "db-secret" not in logged


def test_a_recent_backup_is_not_repeated() -> None:
    fake = FakeRestic(snapshots=[NOW - timedelta(hours=3)])

    assert not run_once(_restic(fake), DATABASE_URL, NOW)

    assert "backup" not in fake.commands


def test_a_backup_after_the_laptop_slept_catches_up() -> None:
    fake = FakeRestic(snapshots=[NOW - timedelta(hours=30)])

    assert run_once(_restic(fake), DATABASE_URL, NOW)


def test_a_failed_dump_stops_before_pruning_and_is_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeRestic(snapshots=[NOW - timedelta(hours=30)])
    fake.backup_exit = 1
    sleeps: list[float] = []

    class Stop(Exception):
        pass

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop

    settings = BackupSettings.model_validate(
        {"database_url": DATABASE_URL, "restic_password": PASSWORD, "restic_repository": "/none"}
    )
    with pytest.raises(Stop):
        run_forever(settings, _restic(fake), clock=lambda: NOW, sleep=sleep)

    assert fake.commands.count("backup") == 2
    assert "forget" not in fake.commands
    assert sleeps == [3600, 3600]
    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == ["Backup failed at backup: exit code 1"] * 2


def test_the_newest_snapshot_time_is_read_with_nanoseconds() -> None:
    fake = FakeRestic(snapshots=[NOW - timedelta(hours=2), NOW - timedelta(hours=5)])

    assert _restic(fake).latest_time() == NOW - timedelta(hours=2) + timedelta(microseconds=123456)
    assert Restic("/backups", PASSWORD, runner=FakeRestic(exists=False)).latest_time() is None


def test_an_unexpected_restic_failure_is_an_error() -> None:
    def broken(args: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 1, b"", b"")

    with pytest.raises(ResticError, match="snapshots failed with exit code 1"):
        Restic("/backups", PASSWORD, runner=broken).latest_time()
    assert backup_age(Restic("/backups", PASSWORD, runner=broken), NOW).startswith(
        "Backup status unavailable"
    )


def _state(counts: dict[str, int], newest: int = 10, migrations: tuple[str, ...] = ("0005",)):
    return DatabaseState(migrations, counts, newest)


def test_restore_comparison() -> None:
    live = _state({"message": 4, "feedback": 1})

    assert compare(live, _state({"message": 4, "feedback": 1})).passed
    changed = compare(_state({"message": 6, "feedback": 1}, newest=12), live)
    assert changed.passed and changed.live_changed
    assert not compare(live, _state({"message": 3, "feedback": 1})).passed
    assert not compare(live, _state({"message": 4})).passed
    assert not compare(live, _state({"message": 4, "feedback": 1}, migrations=("0004",))).passed
    assert compare(live, _state({"message": 3, "feedback": 1})).lines[1] == (
        "message: restored 3, live 4"
    )


def test_the_review_summary_holds_no_message_text() -> None:
    counts = Counts(open=1, closed_test=2, closed_dismissed=0, withdrawn=1)
    flag = OpenFlag(7, "note", "too formal", NOW, "egg?", "Nine minutes. " * 100)
    zone = ZoneInfo("Asia/Jakarta")

    summary = render(counts, [], "Newest backup: 3.0 hours ago", zone)
    full = render(counts, [flag], "Newest backup: 3.0 hours ago", zone)

    assert summary == (
        "Open: 1 · closed as test: 2 · dismissed: 0 · withdrawn: 1\nNewest backup: 3.0 hours ago"
    )
    assert '#7  2026-10-14 19:00  note: "too formal"' in full
    assert "  You:   egg?" in full
    reply_line = full.splitlines()[-1]
    assert len(reply_line) == len("  Reply: ") + 600 and reply_line.endswith("…")
