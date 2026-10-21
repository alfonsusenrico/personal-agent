# Tasks

## 1. Repository

- [x] 1.1 Settings: `*_FILE` secrets, database URL from parts, `AF_REVISION`. Verify:
  settings tests (files read, errors name variables never values, 0640 and 0600 accepted,
  modes open to others or group-writable refused).
- [x] 1.2 Worker health endpoint (loopback): revision, polling, database, conflict.
  Verify: tests for healthy, polling stalled, Telegram 409, database down; no content in
  the response.
- [x] 1.3 Deploy files, the backup hook, framework tools copied from a framework commit,
  Compose for production. Verify: `contracts.py` on both manifests; the hook writes and
  checks a restic snapshot against a throwaway database.
- [x] 1.4 CI: `ci.yml` (Postgres service, `REQUIRE_DB=1`, contract test, both image
  targets), `release.yml`, `af-release.yml`; `default:ci` override removed. Verify:
  actionlint; a green pull request run.
- [x] 1.5 Engine rehearsal on the laptop against a loopback registry: install, upgrade with
  a migration (the hook's backup written and checked), a release reporting the wrong
  revision (restored), health unhealthy on a 409 from a fake Telegram. Verify: rehearsal
  output in `verification.md`.
- [x] 1.6 Docs: deployment guide, `AGENTS.md` (capability `deployed`, the backup debt with
  its payback), README. Verify: `openspec validate --all --strict`.

Expected Result: the engine can deploy the agent unattended and safely.

## 2. Owner-approved steps

- [x] 2.1 Owner: release signing key secrets (owner-run script); the package made public
  after the first publish. Verify: secret names; anonymous read of the image.
- [x] 2.2 Production gateway: `personal_agent`'s contract upgraded to the alias-backed
  version; its production request set to the development values; the owner registers the
  project's OpenAI key (write-only); a production key issued straight into the agent's
  secret store. Verify: the exported contract's
  production request; credential metadata only; secret names and sizes only.
- [x] 2.3 Server: host config, placement, secrets (Telegram token and restic password moved
  by an owner-run script; a new database password). Verify: names, modes, and sizes only.
- [x] 2.4 Cutover: laptop backup service stopped, final backup and sealed dump, restore on
  the server in one transaction. Verify: per-table row counts and a content checksum match.
- [x] 2.5 Tag `v0.1.0` (failed, see verification.md) and then `v0.1.1`; the agent deploys. Verify: deployment record, `/health` commit, the
  Telegram notice. Owner-authorised: one real reply (paid model) — the owner writes to the
  bot and checks the reply, `/profile`, and a 👎 flag.
- [ ] 2.6 After two weeks and with the owner's approval: remove the laptop database
  (backup kept). Verify: the owner's confirmation. Follow-up after archiving, due
  2026-11-04.
- [x] 2.7 `verification.md` complete; push, pull request, report its link; the owner
  merges and decides the deployment.

Expected Result: the assistant runs from the server on the production gateway, with its
history.
