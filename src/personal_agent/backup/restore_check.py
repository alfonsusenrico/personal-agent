"""Restore the newest backup into a throwaway database and compare it with the live one.

`scripts/restore-check` starts the throwaway PostgreSQL and passes its URL in
RESTORE_DATABASE_URL. Only table names, migration versions, and row counts are
printed. When the live database has newer events than the backup, the counts can
only be shown; the check then passes on a clean restore with the same migrations.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

import psycopg
from psycopg import sql
from pydantic import PostgresDsn

from personal_agent.backup.restic import Restic, ResticError, database_environment
from personal_agent.backup.service import EXIT_CONFIG, BackupSettings
from personal_agent.settings import SettingsError, load


class RestoreCheckSettings(BackupSettings):
    restore_database_url: PostgresDsn


@dataclass(frozen=True, slots=True)
class DatabaseState:
    migrations: tuple[str, ...]
    counts: dict[str, int]
    newest_event_id: int


@dataclass(frozen=True, slots=True)
class Comparison:
    passed: bool
    live_changed: bool
    lines: list[str]


def read_state(database_url: str) -> DatabaseState:
    with psycopg.connect(database_url) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for table in tables:
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            row = connection.execute(query).fetchone()
            counts[table] = int(row[0]) if row else 0
        migrations = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        newest = connection.execute("SELECT coalesce(max(id), 0) FROM inbound_event").fetchone()
    return DatabaseState(migrations, counts, int(newest[0]) if newest else 0)


def compare(live: DatabaseState, restored: DatabaseState) -> Comparison:
    live_changed = live.newest_event_id != restored.newest_event_id
    lines = [
        f"{table}: restored {restored.counts.get(table, 'missing')}, live {count}"
        for table, count in sorted(live.counts.items())
    ]
    same_tables = set(live.counts) == set(restored.counts)
    same_migrations = live.migrations == restored.migrations
    if not same_migrations:
        lines.append("migrations differ")
    if not same_tables:
        lines.append("tables differ")
    passed = same_migrations and same_tables and (live_changed or live.counts == restored.counts)
    return Comparison(passed, live_changed, lines)


def restore(restic: Restic, target_url: str) -> int:
    """Pipe the newest dump into pg_restore; the exit code of the first failing step."""
    dump_args, dump_env = restic.dump_command()
    target = database_environment(target_url)
    with subprocess.Popen(  # noqa: S603
        dump_args, env=dump_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ) as dump:
        # pg_restore comes from the image's PATH, which the Dockerfile controls.
        restored = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                target["PGDATABASE"],
            ],
            stdin=dump.stdout,
            env={**dump_env, **target},
            capture_output=True,
            check=False,
        )
        if dump.stdout is not None:
            dump.stdout.close()
        dump_code = dump.wait()
    return dump_code or restored.returncode


def main() -> int:
    try:
        settings = load(RestoreCheckSettings)
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG
    restic = Restic(settings.restic_repository, settings.restic_password.get_secret_value())
    try:
        if restic.latest_time() is None:
            print("No backup to restore")
            return 1
    except ResticError as exc:
        print(exc)
        return 1
    code = restore(restic, str(settings.restore_database_url))
    if code != 0:
        print(f"Restore failed with exit code {code}")
        return 1
    result = compare(
        read_state(str(settings.database_url)), read_state(str(settings.restore_database_url))
    )
    print("\n".join(result.lines))
    if result.live_changed:
        print("The live database changed since the backup: counts are for information.")
    print("Restore check passed" if result.passed else "Restore check FAILED")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
