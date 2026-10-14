"""The backup service: a daily encrypted database backup while the assistant runs.

The laptop the assistant runs on often sleeps at night, so there is no fixed time:
on start and then every hour the service backs up when the newest snapshot is a
day old or there is none. After each backup old snapshots are pruned and the
repository is checked, reading all data, which is cheap at this size.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import Field, SecretStr

from personal_agent.backup.restic import Restic, ResticError
from personal_agent.logging_setup import configure_logging
from personal_agent.settings import Settings, SettingsError, load

BACKUP_EVERY = timedelta(hours=24)
EXIT_CONFIG = 2
logger = logging.getLogger(__name__)


class BackupSettings(Settings):
    """Where the restic repository is and how to open it."""

    restic_repository: str = Field(default="/backups", min_length=1)
    restic_password: SecretStr = Field(min_length=12)
    backup_check_seconds: int = Field(default=3_600, ge=60, le=86_400)


def is_due(latest: datetime | None, now: datetime) -> bool:
    return latest is None or now - latest >= BACKUP_EVERY


def run_once(restic: Restic, database_url: str, now: datetime) -> bool:
    """Back up when one is due. True when a backup was taken."""
    if restic.ensure_repository():
        logger.info("Backup repository created")
    restic.unlock()
    if not is_due(restic.latest_time(), now):
        return False
    started = time.monotonic()
    saved = restic.backup(database_url)
    restic.forget()
    restic.check()
    logger.info(
        "Backup snapshot %s saved (%d bytes added, %.1f s)",
        saved.snapshot_id[:8],
        saved.bytes_added,
        time.monotonic() - started,
    )
    return True


def _protect(repository: str) -> None:
    # Owner-only access to the repository folder: the files are encrypted, but their
    # names, sizes, and times are not.
    if os.path.isdir(repository):
        os.chmod(repository, 0o700)


def run_forever(
    settings: BackupSettings,
    restic: Restic,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    _protect(settings.restic_repository)
    while True:
        try:
            run_once(restic, str(settings.database_url), clock())
        except ResticError as exc:
            logger.error("Backup failed at %s: exit code %d", exc.step, exc.exit_code)
        sleep(settings.backup_check_seconds)


def main() -> int:
    try:
        settings = load(BackupSettings)
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG
    configure_logging(settings)
    restic = Restic(settings.restic_repository, settings.restic_password.get_secret_value())
    logger.info("Backup service started (revision %s)", settings.app_revision)
    run_forever(settings, restic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
