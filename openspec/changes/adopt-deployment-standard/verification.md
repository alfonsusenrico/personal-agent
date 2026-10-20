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

## Not verified yet

- Everything in phase 2 (owner steps, cutover, `v0.1.0`, the Telegram check).
