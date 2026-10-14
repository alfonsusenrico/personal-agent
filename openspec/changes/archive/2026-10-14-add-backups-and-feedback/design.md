# Design

## Context

S1 and S2 store the owner's conversation and profile in PostgreSQL. The only
copies are custom-format dumps made by hand before risky migrations. The
assistant runs on the owner's laptop until S4 (owner decision), so it is often
asleep at night. Daily use leaves no record of bad replies, and the owner
asked that nothing in this change be Telegram-specific: Telegram is the first
channel, and later channels will have their own feedback mechanisms.

## Goals / Non-Goals

**Goals:** a daily encrypted backup that survives laptop sleep, a proven
restore, a channel-neutral record of flagged replies, a Telegram mapping for
it, and a local review.

**Non-Goals:** off-laptop copies, the gateway's database, chat notifications
about backups, adapting replies from flags, a review command in a chat.

## Decisions

### 1. Feedback is a record on the assistant's message

A `feedback` row points at the assistant `message` row it is about, with kind
`dislike` or `note`, the owner's note (for `note`), the inbound event that
created it, and its review state. Channels only map their own gestures to it:
Telegram's 👎 reaction and `/note`; a later web UI might use a button, a voice
channel a spoken phrase. The channel's raw gesture stays in `inbound_event`
like every other input.

Rejected: storing Telegram reaction state as the flag (ties review and later
reflection to one channel); recording a flag as a conversation turn (it would
enter the model's history and change pacing).

### 2. Telegram mapping

- **👎 on an assistant message** creates a `dislike`; changing the reaction to
  anything without 👎 withdraws it (`withdrawn_at`). Other reactions are
  logged as events only. The message is found through the outbox row that
  carries its Telegram message ID; any chunk of a split reply maps to the
  turn's assistant message. A reaction on the owner's own message is logged
  only.
- **`/note <text>`**, sent as a reply to an assistant message, notes that
  message; otherwise it notes the latest sent assistant message. `/note`
  with no text is a `dislike` of that message. The command is acknowledged
  with a 👌 reaction on the `/note` message through the outbox, so it is sent
  once and retried like any reply. No turn, no model call.
- **Menu:** `/start`, `/profile`, `/note`, `/stop`.
- Reactions arrive only when `message_reaction` is listed in
  `allowed_updates`. The Bot API text names an administrator requirement,
  which applies to groups; secondary sources say private chats deliver
  reactions without it. Task 1.1 confirms this on the owner's bot before
  anything else is built; if reactions do not arrive, work stops and the
  owner decides (for example `/note` only).

Rejected: treating every negative-looking emoji as a dislike (ambiguous;
👎 is the explicit gesture); a typed "bad reply" message parsed by the model
(costs a call and can misfire).

### 3. Flags never reach the model

Flags are for the owner's review and later reflection (S5), not for the
current reply. The `/note` event is kept out of the conversation history and
does not count as an owner turn for question pacing.

Rejected: telling the model "the owner disliked your last reply"; without S5
it would only produce apologies and drift.

### 4. Review is local

`./scripts/review` (host, `uv run`, the project settings) lists open flags
oldest first: when, the owner's message before the reply, the reply, and the
note, plus the newest backup's age. `./scripts/review close <id> test|dismissed`
closes one. `./scripts/review --summary` prints counts and backup age only;
that is the only form an agent runs, so the owner's messages appear only on
the owner's terminal. A "test" flag becomes a synthetic conversation script
written from the owner's description.

Rejected: a `/flags` chat command (channel-specific UI, against the owner's
direction); reviewing through an agent session (puts real conversation text
into a model context).

### 5. Backups with restic

The `backup` service streams `pg_dump --format=custom` into a restic
repository with `restic backup --stdin-from-command`, so a failing `pg_dump`
fails the snapshot instead of saving a partial file. After each backup:
`restic forget --keep-daily 7 --keep-weekly 4 --prune`, then `restic check
--read-data` (the database is a few megabytes, so reading all data is cheap).

Rejected: `age` plus our own retention code (more code to get right, no
integrity check); GnuPG (key handling burden); plain dumps (unencrypted
personal data on disk, and in any synced or backed-up home folder); BorgBackup
(similar features, heavier runtime, no stdin-from-command failure handling).

### 6. Version-matched client

The database is PostgreSQL 16. Debian trixie ships client 17, whose dumps
contain `SET transaction_timeout`, which a 16 server rejects on restore. The
image's `backup` build target adds the PostgreSQL project's apt repository
(signed) and installs `postgresql-client-16` and Debian's `restic`. The
worker image is unchanged.

Rejected: client 17 with ignored restore errors (a restore that exits
non-zero cannot prove anything); the `postgres:16-alpine` image with a shell
script (scheduling logic in shell, untested).

### 7. Daily while running

`personal-agent-backup` runs as its own Compose service (restart
`unless-stopped`). On start and then every hour it reads the newest
snapshot's time; when that is 24 hours old or more, or there is none, it
backs up. A laptop that slept through the night backs up within an hour of
waking. One service and restic's repository lock keep a single runner. A
failure is logged with its class and exit code only and retried at the next
hourly check.

Rejected: a fixed nightly time (missed whenever the laptop sleeps); backing
up on every worker start (many snapshots on a busy day, none on a quiet
one).

### 8. Secrets and storage

`BACKUP_DIR` (host path outside the repository, required) is mounted into the
service; the script creates it with mode 700. `RESTIC_PASSWORD` (required) is
generated into `.env` without being shown; the owner copies it into a
password manager. Without it the backups cannot be read, by design.

### 9. Restore check

`./scripts/restore-check` starts a throwaway `postgres:16-alpine` container
on a private Compose network, restores the newest snapshot with `restic dump`
piped to `pg_restore --exit-on-error`, and compares the applied migrations
and every table's row count with the live database. It prints table names
and counts only. When the live database has newer inbound events than the
snapshot, the counts are shown for information and the check passes on a
clean restore with the same migrations; otherwise the counts must be equal.
The throwaway container and its volume are always removed.

### 10. Data model (migration `0005`)

- `inbound_event.kind` gains `reaction`.
- `feedback`: `id`, `person_id`, `message_id` (assistant message), `kind`
  (`dislike`, `note`), `note` (bounded, only for `note`), `source_event_id`,
  `created_at`, `withdrawn_at`, `closed_at`, `outcome` (`test`,
  `dismissed`; set exactly when closed). One open `dislike` per message.
- `outbox`: a `reaction` row may have no turn when it acknowledges a
  command.

## Risks / Trade-offs

- **Reactions in private chats** may not be delivered: checked first
  (task 1.1).
- **Backups live on the same disk** as the database: they protect against
  data mistakes and bad migrations, not disk loss. Off-laptop copies are a
  later decision.
- **A lost password** makes every backup unreadable: the owner keeps a copy.
- **The `backup` image** carries a third-party apt repository: pinned by its
  signing key, used only in that build target.

## Migration Plan

`0005` only adds a kind, a table, and relaxes one outbox check; it applies on
the S2 schema and re-applies as a no-op. Rollback: stop the `backup` service
and drop the `feedback` table; the added kind is unused without the code. A
manual backup is taken before `0005` is applied to the owner's database.
