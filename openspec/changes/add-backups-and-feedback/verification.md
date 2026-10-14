# Verification: add-backups-and-feedback

Status: verified locally, including the owner's live check on Telegram
(2026-10-14).

Branch `feat/backups-and-feedback` on `main` at `1f9e27c`, 2026-10-14. Local
only: macOS host, Docker Compose, PostgreSQL 16. No model calls in this change.

## Data

Synthetic test fixtures; the owner's local database for the migration,
backup, and restore check (only counts and table names were read).

## Results

| Check | Evidence | Result |
|---|---|---|
| Reactions arrive in a private chat (task 1.1) | probe on the owner's bot with `message_reaction` in `allowed_updates`; the owner reacted 👎: one update, chat type private, user present, old `[]`, new `['👎']` | Pass |
| Migration `0005` applies, re-applies as a no-op; one open dislike per message; note and outcome shape; acknowledgements without a turn | `tests/test_migrate.py`, `tests/test_schema.py` | Pass |
| Telegram mapping: 👎 added flags, removed withdraws, other reactions only logged; strangers, groups, anonymous ignored; `/note` with text, as a reply, addressed to the bot, empty; `/notes …` stays a message; an edited command is logged, not answered | `tests/test_updates.py` | Pass |
| End to end: 👎 flags once with no model call and no history entry, withdrawal, reaction on the owner's own message ignored, any chunk of a split reply flags the whole reply, `/note` on the pointed reply or the latest (including a note pointing at the owner's message), 👌 acknowledgement, no turn, no effect on question pacing, no reply text in logs, review listing and closing | `tests/test_feedback_flow.py` (8 tests) | Pass |
| Backups: due logic (none, 24 h, 30 h, 3 h), command lines (stdin-from-command `pg_dump`, host name, retention 7/4, check with read-data, no cache), passwords only in the environment and never in arguments or logs, a failed dump stops before pruning and retries hourly | `tests/test_backup.py` (fake restic) | Pass |
| Review output: summary has counts and backup age only; listing shows time, note, the owner's message, a shortened reply | `tests/test_backup.py` | Pass |
| Backup image | `pg_dump` 16.15 and `pg_restore` 16.15 (PostgreSQL apt repository), restic 0.18.0, runs as uid 10001 | Pass |
| Owner's database: manual dump before `0005` (`research/backups/2026-10-14-before-0005.dump`, mode 600, 15 tables listed), `0005` applied by the migrate step, worker and backup on `2fed167`, menu updated with `/note` | migrate and service logs | Pass |
| First backup | repository created, snapshot saved (67,099 bytes added, 2.5 s), check passed; folder `drwx------`, data files `-r--------` | Pass |
| Restore check | `./scripts/restore-check`: 16 tables, every count equal, migrations equal; throwaway container removed | Pass |
| A failing dump leaves no snapshot | on a copy of the repository, a backup with an unreachable database failed at `backup` (exit 1); snapshots before 1, after 1 | Pass |
| A corrupted repository fails the restore | on a copy with damaged data files: "Restore failed with exit code 1"; throwaway container removed | Pass |
| Documented manual restore | the README command run twice against a throwaway database: both exit 0, data present | Pass |
| Service stop and restart | stops in 0.15 s (`init: true`); after a restart no backup is repeated within 24 hours | Pass |
| `./scripts/review --summary`; closing an unknown ID | counts and backup age; "#999 is not open", exit 1 | Pass |
| `./scripts/setup-local` on a fresh copy | generates `RESTIC_PASSWORD` (43 characters), sets `BACKUP_DIR`, creates it with mode 700; `.env` mode 600 | Pass |
| `./scripts/check` at `85e53af` | lock, format, lint, pyright 0, 211 tests, compose, OpenSpec strict valid | Pass |

## Found and fixed during verification

- The first versions of `scripts/review` and `scripts/restore-check` used
  `docker compose run`, which built and ran a stale `personal-agent:local`
  image whose migrate step correctly refused to run ("Applied migrations
  have no file: 0004, 0005"); nothing changed. Both scripts now run inside
  the running backup service, so they always use the deployed revision.

## Live check (task 6.1)

The owner on Telegram Desktop, 2026-10-14, worker and backup on `2fed167`;
the review and closing were checked by the agent with content-free queries
and `--summary` only.

| Check | Evidence | Result |
|---|---|---|
| 👎 on a reply, then replaced with 👍 | feedback 1 `dislike` withdrawn; three `reaction` events, none in a turn; no reply sent | Pass |
| 👎 kept on another reply | feedback 2 open `dislike` on that reply | Pass |
| `/note too long` as a reply to that answer | feedback 3 `note` (8 characters) on the same reply; 👌 sent through the outbox without a turn; no model call (the 2 calls in the window are the owner's 2 questions) | Pass |
| Menu | `getMyCommands`: `start`, `profile`, `note`, `stop` | Pass |
| Review | `--summary`: 2 open, 1 withdrawn, newest backup 0.1 hours old; `close 2 dismissed` closed it; `close 1 test` on the withdrawn flag: "#1 is not open", exit 1; summary then 1 open, 1 dismissed | Pass |
| Logs | feedback lines carry IDs and kinds only; no note or reply text found; one transient `Polling failed: ReadTimeout`, retried by the poller | Pass |

Not exercised live: `/note` sent without replying (covered by
`tests/test_feedback_flow.py`), the phone client, and the full review
listing, which prints the owner's messages and is left to the owner's own
terminal (its format is covered by `tests/test_backup.py`).
