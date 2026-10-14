"""The restic commands behind backups, the review's backup age, and the restore check.

restic reads the repository and password from the environment, never from
arguments, and `pg_dump` reads the database login the same way, so neither shows
in a process listing. Only exit codes are logged: restic's output names paths and
snapshot IDs, never database content, but nothing here needs it.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from psycopg.conninfo import conninfo_to_dict

# Every snapshot is recorded under this host name: the container's own name changes
# on every start, and retention groups snapshots by host.
SNAPSHOT_HOST = "personal-agent"
DUMP_FILENAME = "personal_agent.dump"
KEEP_DAILY = 7
KEEP_WEEKLY = 4
# restic's exit code when the repository does not exist yet.
_NO_REPOSITORY = 10

Runner = Callable[[list[str], Mapping[str, str]], subprocess.CompletedProcess[bytes]]


class ResticError(RuntimeError):
    def __init__(self, step: str, exit_code: int) -> None:
        super().__init__(f"restic {step} failed with exit code {exit_code}")
        self.step = step
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class Saved:
    snapshot_id: str
    bytes_added: int


def run_command(args: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, env=dict(env), capture_output=True, check=False)  # noqa: S603


def database_environment(database_url: str) -> dict[str, str]:
    """libpq variables for the database URL, so the password stays out of arguments."""
    names = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "password": "PGPASSWORD",
        "dbname": "PGDATABASE",
    }
    parts = conninfo_to_dict(database_url)
    return {names[key]: str(value) for key, value in parts.items() if key in names and value}


class Restic:
    def __init__(self, repository: str, password: str, *, runner: Runner = run_command) -> None:
        self._env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "RESTIC_REPOSITORY": repository,
            "RESTIC_PASSWORD": password,
        }
        self._runner = runner

    def _run(
        self, step: str, args: list[str], extra_env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        result = self._runner(["restic", "--no-cache", *args], {**self._env, **(extra_env or {})})
        if result.returncode != 0:
            raise ResticError(step, result.returncode)
        return result

    def latest_time(self) -> datetime | None:
        """When the newest snapshot was taken; None when there is none or no repository."""
        result = self._runner(
            [
                "restic",
                "--no-cache",
                "snapshots",
                "--json",
                "--host",
                SNAPSHOT_HOST,
                "--latest",
                "1",
            ],
            self._env,
        )
        if result.returncode == _NO_REPOSITORY:
            return None
        if result.returncode != 0:
            raise ResticError("snapshots", result.returncode)
        snapshots = json.loads(result.stdout or b"[]")
        if not snapshots:
            return None
        return max(datetime.fromisoformat(snapshot["time"]) for snapshot in snapshots)

    def ensure_repository(self) -> bool:
        """Create the repository when it does not exist. True when it was created."""
        result = self._runner(["restic", "--no-cache", "cat", "config"], self._env)
        if result.returncode == 0:
            return False
        if result.returncode != _NO_REPOSITORY:
            raise ResticError("cat config", result.returncode)
        self._run("init", ["init"])
        return True

    def unlock(self) -> None:
        """Remove stale locks left by a run that was stopped mid-way."""
        self._run("unlock", ["unlock"])

    def backup(self, database_url: str) -> Saved:
        """Stream a custom-format dump into a snapshot. A failing dump fails the backup
        and leaves no snapshot."""
        result = self._run(
            "backup",
            [
                "backup",
                "--json",
                "--host",
                SNAPSHOT_HOST,
                "--tag",
                "database",
                "--stdin-filename",
                DUMP_FILENAME,
                "--stdin-from-command",
                "--",
                "pg_dump",
                "--format=custom",
            ],
            database_environment(database_url),
        )
        for line in reversed(result.stdout.splitlines()):
            message = json.loads(line)
            if message.get("message_type") == "summary":
                return Saved(message["snapshot_id"], int(message.get("data_added", 0)))
        raise ResticError("backup summary", 0)

    def forget(self) -> None:
        self._run(
            "forget",
            [
                "forget",
                "--host",
                SNAPSHOT_HOST,
                "--keep-daily",
                str(KEEP_DAILY),
                "--keep-weekly",
                str(KEEP_WEEKLY),
                "--prune",
            ],
        )

    def check(self) -> None:
        self._run("check", ["check", "--read-data"])

    def dump_command(self) -> tuple[list[str], dict[str, str]]:
        """The command and environment that write the newest dump to standard output."""
        return (
            [
                "restic",
                "--no-cache",
                "dump",
                "--host",
                SNAPSHOT_HOST,
                "latest",
                f"/{DUMP_FILENAME}",
            ],
            dict(self._env),
        )
