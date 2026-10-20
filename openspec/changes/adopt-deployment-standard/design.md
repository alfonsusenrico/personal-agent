# Design

## Context

Compose today: `db` (Postgres 16), `migrate` (one-shot), `worker` (the bot), and `backup`
(daily restic snapshots into `BACKUP_DIR`); images from the `runtime` and `backup` targets
of one Dockerfile. Settings come from environment variables, with the secrets in `.env`.
The worker holds a Postgres advisory lock, so one worker per database; Telegram allows one
`getUpdates` consumer per bot, and the poller already waits on a 409 conflict. The server
runs the deployment standard's engine, pull agent, and monitor, and the production gateway
(0.13.0) on its own Compose network, named in the host's placement.

## Decisions

### 1. Release shape

- Images: `app` (the `runtime` target: migrate and worker) and `backup` (the `backup`
  target), built once in CI for amd64 and arm64.
- `switch = "stop-start"`, `drain_seconds = 30`: the worker stops between steps; a reply
  in progress is finished after the restart by the existing exactly-once rules (the
  gateway call is waited for, never paid twice).
- `migrate` runs `personal-agent-migrate` with its dependencies; production
  `backup = "required"`, so every release with a migration first runs the backup hook.
- `keep = 5` releases.

Rejected: blue-green (two pollers for one bot would split updates).

### 2. Worker health

The worker serves `GET /health` on a container port (8080), published on a loopback port
only: `{"status", "revision", "polling", "database"}`, 200 when the last poll ended within
the last 90 seconds without a conflict and the database answers, 503 otherwise (no
content, no identifiers beyond the revision). The engine checks it after each start; the
monitor checks it every few minutes and notifies `unhealthy` and `recovered`. It is a few
lines of asyncio stream handling (no web framework).

Rejected: a container `HEALTHCHECK` only (the standard's engine and monitor need the
revision over HTTP); a framework change for command-based health checks (the agent needs
"polling works", which only the worker knows).

### 3. Settings and secrets

- Secrets as files (0640, the engine's `group_add`): `POSTGRES_PASSWORD`,
  `TELEGRAM_BOT_TOKEN`, `GATEWAY_API_KEY`, `RESTIC_PASSWORD`; the settings accept
  `NAME_FILE` for each, and the database URL is built from `POSTGRES_*` parts, so no URL
  with a password is ever written.
- Placement (host, never committed): `TELEGRAM_ALLOWED_USER_IDS`, `BACKUP_DIR`,
  `AGENT_HEALTH_PORT`, `AGENT_GATEWAY_NETWORK`.
- Committed defaults (`deploy/env/production.env`): `GATEWAY_URL=http://api:8000`,
  `REPLY_PROFILE=reply`, `DEFAULT_TIMEZONE=Asia/Jakarta`, and the conversation tunables
  as today.
- `APP_REVISION` becomes the build argument `AF_REVISION`, baked into the image and
  reported by `/health`.

### 4. Backups

- Before a migration, `deploy/hooks/backup` starts `db`, streams `pg_dump` into
  `restic backup --stdin` in a new repository in `$AF_BACKUP_DIR` (password from the
  `RESTIC_PASSWORD` secret), and runs `restic check`; a failure stops the deployment.
- The daily backup service runs in production as on the laptop, into `BACKUP_DIR` on the
  server, with the same retention and checks.
- **Debt (owner decision):** no off-host copy yet. Payback: a follow-up change adds a
  restic copy to a second location after the agent has run in production for two weeks;
  until then a server disk loss loses everything after the sealed pre-move dump.

### 5. The gateway

The worker joins the production gateway's network and calls `http://api:8000` with the
`personal_agent` project's production key, issued on the gateway straight into the secret
store. The project's contract is first upgraded to the alias-backed version
(`project upgrade-contract personal_agent`): it is still version 1, which the gateway
refuses; the upgrade appends a version with each profile mapped to its catalogue alias
(`gpt-6-luna` to `openai-gpt-6-luna`, `stub-echo` to `stub-echo`) and keeps the history. The production request is set to the development values (30/min, 2 parallel,
USD 5 a month, USD 0.05 per call), and the owner registers the project's OpenAI key on
the production gateway (write-only command); the `reply` and `reply_stub` profiles
already exist in the project's contract.

### 6. Cutover and data

One bot, one poller, one database:

1. The laptop worker stays stopped (it is today); stop the laptop backup service.
2. Take a final laptop backup and a sealed dump; restore it into the server database in
   one transaction; compare row counts per table and a content checksum.
3. First deployment (`v0.1.0`): the migration step finds the schema current.
4. The owner sends a message on Telegram and gets a reply; flags and `/profile` work.
5. The laptop database stays stopped as a fallback for two weeks, then is removed with
   the owner's approval; the laptop keeps the code, the tests, and the stub scripts.

Turn call keys are random UUIDs stored on the rows, so the moved data never reuses a call
ID the gateway remembers.

Rejected: running laptop and server in parallel for a while (two pollers, two databases,
split history).

### 7. CI

`ci.yml` (pull requests, and called by `release.yml`): `uv sync --locked`, a Postgres 16
service with `REQUIRE_DB=1`, `./scripts/check`, the gateway contract test against the
pinned image, and builds of both image targets. `release.yml`/`af-release.yml` as the
gateway and playground use them. The release package on GHCR is public, like the
repository; it holds no secrets or host details.

## Risks

- **Server outage:** the bot is down while the server is; Telegram keeps updates for 24
  hours and the offline notice already explains delays.
- **Single location for data:** see the backup debt.
- **Cost:** unchanged per message; the production grant caps a month at USD 5.

## Rollback

A failed release restores the previous one (same schema) or stops for the owner (schema
changed; restore from the pre-migration backup). Moving back to the laptop is the
cutover in reverse: stop the server worker, dump, restore on the laptop.
