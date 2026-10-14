# Tasks

## 1. Reaction delivery and data model

- [x] 1.1 Owner-assisted probe: add `message_reaction` to `allowed_updates`
  on a scratch worker run, the owner reacts 👎 to one reply in the private
  chat. Verify: the update arrives (kind and IDs logged, no content). If it
  does not arrive, stop and report to the owner.
- [x] 1.2 Migration `0005`: the `reaction` event kind, the `feedback` table,
  and command acknowledgements without a turn. Verify with migration tests
  (applies on the S2 schema, re-applies as a no-op) and schema tests (one
  open dislike per message, outcome set exactly when closed, bounded note).

Expected Result: reactions are confirmed deliverable and the schema holds
flags.

## 2. Feedback

- [x] 2.1 Feedback store: flag, withdraw, note, close, list open, counts.
  Verify with database tests for each operation.
- [x] 2.2 Telegram mapping: parse `message_reaction` into a `reaction`
  envelope (logged, never answered); 👎 added or removed on an assistant
  message (any chunk) flags or withdraws; `/note` with and without text, as a
  reply and not; 👌 acknowledgement through the outbox; menu adds `/note`.
  Verify with fake-server flow tests, including no model call, no history
  entry, no effect on question pacing, and no note text in logs.

Expected Result: a flag or note is recorded once and leaves the conversation
untouched.

## 3. Review

- [x] 3.1 `./scripts/review` (list, close, `--summary`). Verify with tests on
  synthetic data: ordering, context lines, closing, and a summary with no
  message text.

Expected Result: the owner can review and close flags locally.

## 4. Backups

- [x] 4.1 `backup` build target (PostgreSQL apt repository with its signing
  key, `postgresql-client-16`, `restic`) and Compose service with
  `BACKUP_DIR` and `RESTIC_PASSWORD`. Verify: `docker compose config -q`,
  image builds, `pg_dump --version` reports 16.
- [x] 4.2 `personal-agent-backup`: due check (none, 30 hours, 3 hours),
  backup with `--stdin-from-command`, forget and prune, check, hourly loop,
  content-free logs. Verify with unit tests for the due logic and command
  lines, and a test where a failing dump leaves no snapshot (fake restic).
- [x] 4.3 `./scripts/restore-check`. Verify: on the test stack, a restore
  matches counts; a corrupted repository fails and the throwaway container
  is removed.
- [x] 4.4 Owner's database: manual dump to `research/backups/`, apply
  `0005`, generate `RESTIC_PASSWORD` into `.env` without printing, start the
  service. Verify: one snapshot exists, `restic check` passes,
  `./scripts/restore-check` passes, the folder is mode 700.

Expected Result: the owner's database is backed up daily and a restore is
proven.

## 5. Documentation

- [x] 5.1 `README.md` (backups, restore, review, the password copy),
  `AGENTS.md` (architecture map; agents run only `./scripts/review
  --summary`), `.env.example`. Verify: `./scripts/check` passes.

Expected Result: the owner can restore and review from the README alone.

## 6. Live verification and delivery

- [ ] 6.1 Owner live check on Telegram: 👎 then removed, 👎 kept, `/note`
  as a reply and after a reply, the menu; then `./scripts/review` and close
  one flag. Verify: results and a log scan with no note or message text in
  `verification.md`.
- [ ] 6.2 Run `./scripts/check`, open a PR for the branch, and report its
  URL and the verification record; the owner merges.
