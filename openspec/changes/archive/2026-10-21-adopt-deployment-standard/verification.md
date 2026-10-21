# Verification

Branch `feat/deployment-standard`, based on `429f15b`, 2026-10-20. Phase 1 (repository).

| Check | Result |
|---|---|
| `./scripts/check` (`REQUIRE_DB=1`) | passed: lock, format, lint, Pyright, 250 tests (2 skipped: the opt-in contract tests, run below), both Compose combinations with synthetic values, both deploy manifests (`contracts.py`), OpenSpec |
| Settings tests | secrets and the database URL from files; 0640 and 0600 accepted; 0644, 0660, 0604 refused; a value and its file together refused; missing, empty, and unreadable files named; missing database parts named; empty variables count as unset; errors never contain values |
| Health tests | 200 with exactly `status`, `revision`, `polling`, `database`; 503 before the first poll, after 90 seconds without a poll, on a Telegram 409, and with the database down; over HTTP: 404 elsewhere, 405 for POST, `Cache-Control: no-store`; the poller records success and conflict |
| Contract test (`PA_CONTRACT_IMAGE` = gateway 0.13.0) | 2 passed |
| Images (`--target runtime` and `--target backup`, `AF_REVISION` = the commit) | the runtime image has no gateway package, imports the worker, and bakes the commit as `APP_REVISION`; the backup image has restic 0.18.0 and pg_dump 16.15 |
| actionlint 1.7.12 on the workflows; shellcheck 0.11.0 on the hook and scripts | clean |
| Daily backup service under production settings (read-only root, dropped capabilities, file secrets, database URL from parts, throwaway database) | repository created, snapshot saved and checked |

## Engine rehearsal (task 1.5)

On the laptop (Docker Desktop, arm64): a loopback `registry:3`, a throwaway cosign key,
synthetic secrets, a fake Bot API on the gateway network, `af_publish.py --local` from copies
of the working tree (pointing the worker at the fake Bot API), and `af_engine.py` against a
temporary base directory. Commits are synthetic (`aaaa…` to `dddd…`).

| Step | Result |
|---|---|
| A: install | `deployed`, exit 0; migrations 0001-0005 applied; `/health` 200 reporting `aaaa`; the worker runs as `agent` with a read-only root and reads its three secrets through the group |
| B: upgrade with a migration (0006) | `deployed`, exit 0, `/health` reports `bbbb`; the pre-migration backup is a restic repository in `<base>/backups/<time>/`; restored into a throwaway database it has the same 16 tables and row counts as before the release (3 seeded rows kept live) |
| C: release reporting the wrong revision (same schema) | `restored`, exit 3; `/health` reports `bbbb` |
| 409 from the Bot API (a second consumer) | `/health` 503 with `polling: false` within 1 s; 200 again 31 s after the conflict ended |
| D: release with a migration (0007) whose backup fails (wrong database password) | `pg_dump` refused, restic saved nothing, `stopped`, exit 5; migration 0007 not applied, data unchanged; with the password back the same release deployed (`dddd`) |
| Secret values in engine output | none |

All rehearsal containers, volumes, networks, the registry, and images were removed. Script
and output are kept in the ignored `research/evidence/adopt-deployment-standard/`.

The first rehearsal run stopped at C because each source copy lacked the earlier rehearsal
migration; the migration runner refused (`Applied migrations have no file: 0006`) and the
engine stopped for the owner (exit 5), as it should. The script was fixed and rerun.

## Engine behaviour recorded

- The engine runs the backup hook only when a previous release exists, so the first
  deployment takes no pre-migration backup; the laptop's final backup covers it.
- A failed backup before a schema change leaves the services stopped for the owner (exit 5)
  even though nothing migrated. Restoring the previous release there would be safe; this is
  a possible framework improvement, not needed for this change.

## Pull request and release

- PR #13: CI passed (checks with Postgres, contract test, both images), 1 min 41 s.
- `deploy/af/` matches agent-framework `59cd9c4` (PR #36 merged; identical to the branch
  commit `d9f9076` it was first copied from).
- The first publish from `main` (`5dc604d`) failed: #13 merged before the signing-key
  secrets existed, and a run reads secrets when it starts. The secrets were stored by the
  owner's script and the failed job was re-run; the public key is committed in
  `deploy/release-signing.pub`.
- Repository secrets `COSIGN_PRIVATE_KEY` and `COSIGN_PASSWORD` exist (names only). The
  packages `personal-agent`, `personal-agent/app`, and `personal-agent/backup` answer an
  anonymous pull with 200 (public with the repository).

## Production preparation (2026-10-21)

- Gateway: `personal_agent` upgraded to contract version 2 (`reply` names
  `openai-gpt-6-luna`); production request 30 rpm, concurrency 2, USD 5.00, ceiling USD
  0.05; OpenAI credential `configured: true` (metadata only). The gateway shares one
  monthly pool, so production was granted USD 1.375; on the owner's choice the unused
  development request on the production gateway was set to zero, and production is now
  granted USD 2.75, 27 rpm, concurrency 1.
- Server (owner-run script): host config and public key 0640 root:af; base 0700 and
  `placement.env` 0600 af:af with the four placement names; daily backup folder uid and gid
  10001, 0700; secrets `GATEWAY_API_KEY`, `POSTGRES_PASSWORD`, `RESTIC_PASSWORD`,
  `TELEGRAM_BOT_TOKEN` 0640 af:af (sizes only checked). The production key was issued on
  the server straight into the store.
- Cutover: the laptop worker had been stopped since 2026-10-19. The laptop's restic writes
  to its bind-mounted repository fail since a Docker restart (`sync ...: bad file
  descriptor`, retried forever; last good snapshot 2026-10-20), so the final snapshot went
  to a Docker volume on the laptop (checked); the laptop backup service is stopped. A
  pre-seeded production volume with the Compose labels received the laptop dump streamed
  over SSH into `pg_restore --single-transaction --exit-on-error --no-owner`: 16 tables
  (row count and content md5) and 13 sequences match. Compose 5.6.0 on the server keeps
  such a volume and its data (probed with a throwaway project).

## First deployment: v0.1.0 failed

- The engine started the database and ran the migration in a `worker` container, which
  failed with `OperationalError`; as a first deployment, everything stayed stopped and the
  owner was notified. The agent's database logged no connection; the gateway's database
  logged `password authentication failed for user "personal_agent"` / `Role
  "personal_agent" does not exist`. The worker also joins the gateway's network, where
  `db` is the gateway's database, and Docker's resolver answered with that one.
- Fix (`v0.1.1`): the agent's database has the alias `personal-agent-db` on its own
  network and `DATABASE_HOST` uses it; `scripts/check` refuses a production
  `DATABASE_HOST` that is `db` or not an alias of `db` (it fails on the old file). A laptop
  probe with a stand-in gateway network holding its own `db` showed, from the worker,
  `db` resolving to the stand-in and `personal-agent-db` to the agent's database. The
  rehearsal missed it because its stand-in gateway network had no `db`.
- Nothing was written to either database; the production volume was untouched.

## Production deployment: v0.1.1 (2026-10-21)

- Release run for the tag: `tag-version` and `promote v0.1.1` passed after the `main`
  publish of `43efedc`. The server's agent deployed it at 01:30:34Z: `deployed`, exit 0.
- `/health`: 200, `{"status":"ok","revision":"43efedc…","polling":true,"database":true}`.
  Worker, backup service, and database running; the worker logged the production gateway
  project and its revision. Migrations: 5 recorded, the schema current; 30 turns and 60
  messages, as on the laptop.
- The server's daily backup service created its repository and saved a snapshot at start.
- Owner check on Telegram: `/profile` showed the moved profile. The first two messages were
  answered with the apology: the gateway refused them with `cost_ceiling_exceeded`.
  Gateway 0.13.0 reserves the full-context worst case for `openai-gpt-6-luna` (1,050,000
  input tokens at the long-context rate plus the 2,000-token output: about USD 0.264), above
  the USD 0.05 per-call ceiling copied from development. The owner set the production
  ceiling to USD 0.30 (it admits every call of this profile and refuses a configuration
  that reserves more). Then three replies completed (USD 0.000475, 0.000347, 0.000271),
  the reply used the profile and recent conversations, and the owner's 👎 was stored as
  feedback 4.

## Not verified yet

- 2.6: the laptop database stays as the fallback until 2026-11-04; its removal needs the
  owner's approval then.
