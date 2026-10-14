# Proposal

## Why

The assistant now keeps personal memory (the profile, and from S3 on, recalled
conversations and facts) that the owner will rely on for weeks, but the
database has no backup beyond copies made by hand before migrations. Quality
is also judged only in planned live checks: a bad reply in daily use leaves no
trace. The roadmap promised both foundations by S2; they come before the
memory stages (goal review, 2026-10-14).

## Expected Outcome

- **Backups:** while the assistant runs, its database is backed up once a
  day, encrypted, on the owner's laptop, keeping 7 daily and 4 weekly copies.
  A day the laptop was asleep is caught up when it wakes. A restore check
  proves the newest backup restores with the same data.
- **Flagging a bad reply:** the owner reacts 👎 to a reply, or sends
  `/note <what was wrong>` (as a reply to that message, or right after it).
  The flag is recorded with no model call and no change to the conversation;
  `/note` is acknowledged with a 👌 reaction. Removing the 👎 withdraws the
  flag.
- **Review:** the owner runs `./scripts/review` on the laptop: the open
  flags (when, the owner's message, the reply, the note) and the age of the
  newest backup. Each flag is closed as "test" (it becomes a synthetic
  regression script) or "dismissed".
- **Channel-neutral:** flags are stored as feedback on the assistant's
  message, not as Telegram data. Telegram maps its 👎 reaction and `/note` to
  them; a later channel maps its own mechanism to the same record.

## Owner decisions

2026-10-14 (goal review): a separate change before S3; backups daily while
the agent runs (not at a fixed time); kept on the laptop only; anything
implemented must not be Telegram-specific, because other platforms will
have different mechanisms.

Proposed here for approval (the owner chose neither listed review option and
asked for platform neutrality): flags are reviewed in a local terminal, not
in a chat command; review output shows the owner's own messages only on the
owner's terminal, and the agent turns a "test" flag into a synthetic script
from the owner's description, without reading the conversation.

## What Changes

- **Backup service:** a `backup` Compose service on the agent image runs
  `pg_dump` into a [restic](https://restic.net) repository in a local folder
  outside the repository: encrypted, checked after each run, pruned to 7
  daily and 4 weekly snapshots. The image gains the Debian `restic` and
  `postgresql-client` packages.
- **Restore check:** `./scripts/restore-check` restores the newest snapshot
  into a throwaway PostgreSQL container and compares every table's row count
  with the live database.
- **Feedback record:** migration `0005`: the `reaction` inbound event kind
  and a `feedback` table (the assistant message, kind `dislike` or `note`,
  the owner's note, the source event, withdrawn, closed, outcome).
- **Telegram mapping:** `message_reaction` updates are polled; 👎 on an
  assistant message flags it, removing it withdraws the flag; other
  reactions are logged only. `/note` is a code-handled command that targets
  the replied-to assistant message or the latest reply. The menu adds
  `/note`.
- **Review:** `./scripts/review` lists open flags and backup age, and closes
  a flag with an outcome.

## Non-goals

- The assistant adapting from flags (S5 reflection); flags never reach the
  model.
- Off-laptop copies; backup of the gateway's own database.
- Notifications about backups in a chat; the review shows backup age.
- A review command inside Telegram.
- Positive feedback flags; other reactions stay in the event log for later.

## Capabilities

### New Capabilities

- `reply-feedback`: flagging a reply, withdrawing a flag, and the review.
- `backups`: daily encrypted backups, retention, and the restore check.

### Modified Capabilities

- `message-log`: the event kinds now include reactions and commands.
- `telegram-channel`: the owner's reactions are received; `/note`; the menu.

### Project capabilities and standards

No new project capability. `background-jobs` applies to the backup service
(single runner, catch-up after downtime, bounded failure); `personal-data`
applies to backups (encrypted, local, mode 700 folder) and to notes (logs
hold identifiers and counts only).

## Cost

No model calls. Disk: one compressed, deduplicated snapshot a day of a
database currently a few megabytes.

## Impact

- New: `src/personal_agent/backup/`, `src/personal_agent/feedback/`,
  migration `0005_reply_feedback.sql`, `scripts/review`,
  `scripts/restore-check`.
- Changed: Telegram update parsing and polling, command handling, the menu,
  `Dockerfile` (two Debian packages), `compose.yaml` (the `backup` service),
  `.env.example` (`BACKUP_DIR`, `RESTIC_PASSWORD`), `README.md`, `AGENTS.md`.
- Owner actions: keep a copy of `RESTIC_PASSWORD` in a password manager
  (without it the backups cannot be read).
