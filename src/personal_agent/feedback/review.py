"""Review flagged replies on the owner's own terminal.

`scripts/review` lists open flags and notes with the owner's message and the reply,
plus the newest backup's age; `close <id> test|dismissed` closes one; `--summary`
prints counts and the backup age only, the one form an agent may run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from personal_agent.backup.restic import Restic, ResticError
from personal_agent.backup.service import EXIT_CONFIG, BackupSettings
from personal_agent.feedback import store
from personal_agent.settings import SettingsError, load

# Long replies are cut for reading; the full text stays in the database.
SHOWN_CHARS = 600


class ReviewSettings(BackupSettings):
    default_timezone: str = "Asia/Jakarta"


def _shorten(text: str | None) -> str:
    if text is None:
        return "(none)"
    flat = " ".join(text.split())
    return flat if len(flat) <= SHOWN_CHARS else flat[: SHOWN_CHARS - 1] + "…"


def render(
    counts: store.Counts, flags: list[store.OpenFlag], backup_line: str, zone: ZoneInfo
) -> str:
    lines = [
        f"Open: {counts.open} · closed as test: {counts.closed_test}"
        f" · dismissed: {counts.closed_dismissed} · withdrawn: {counts.withdrawn}",
        backup_line,
    ]
    for flag in flags:
        when = flag.created_at.astimezone(zone).strftime("%Y-%m-%d %H:%M")
        label = f'note: "{flag.note}"' if flag.note else "👎"
        lines += [
            "",
            f"#{flag.id}  {when}  {label}",
            f"  You:   {_shorten(flag.owner_text)}",
            f"  Reply: {_shorten(flag.reply_text)}",
        ]
    return "\n".join(lines)


def backup_age(restic: Restic, now: datetime) -> str:
    try:
        latest = restic.latest_time()
    except ResticError as exc:
        return f"Backup status unavailable ({exc})"
    if latest is None:
        return "No backup yet"
    hours = (now - latest).total_seconds() / 3600
    return f"Newest backup: {hours:.1f} hours ago"


async def _review(settings: ReviewSettings, args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    zone = ZoneInfo(settings.default_timezone)
    restic = Restic(settings.restic_repository, settings.restic_password.get_secret_value())
    async with await AsyncConnection.connect(str(settings.database_url)) as connection:
        if args.action == "close":
            async with connection.transaction():
                closed = await store.close(connection, args.id, args.outcome, now=now)
            print(f"Closed #{args.id} as {args.outcome}" if closed else f"#{args.id} is not open")
            return 0 if closed else 1
        counts = await store.counts(connection)
        flags = [] if args.summary else await store.open_flags(connection)
    print(render(counts, flags, backup_age(restic, now), zone))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="scripts/review")
    parser.add_argument("--summary", action="store_true", help="counts and backup age only")
    actions = parser.add_subparsers(dest="action")
    close = actions.add_parser("close", help="close an open flag")
    close.add_argument("id", type=int)
    close.add_argument("outcome", choices=["test", "dismissed"])
    args = parser.parse_args()
    try:
        settings = load(ReviewSettings)
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG
    return asyncio.run(_review(settings, args))


if __name__ == "__main__":
    raise SystemExit(main())
