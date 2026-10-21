# Proposal

## Why

The agent runs only on the owner's laptop, so it answers only while the laptop is awake,
and it still talks to the laptop's development gateway. The production gateway now runs on
the owner's server under the owner's deployment standard (agent-framework ADR 0009), and
the owner decided on 2026-10-20 to move the apps onto it, the playground first (done) and
then this agent; the laptop stays for development. This supersedes "laptop until S4" in
the 2026-10-14 goal review.

## Expected Outcome

- The owner's assistant answers on Telegram around the clock from the server, with the
  same conversation history, profile, and feedback as on the laptop.
- Every merge to `main` that passes hosted CI publishes a signed release; a `vX.Y.Z` tag
  promotes it, and the server's pull agent deploys it: a backup before migrations, a
  health check that proves the running commit, restore on failure, and a Telegram notice
  for every result.
- If the bot stops polling (for example because another worker took the bot), the server's
  monitor tells the owner on Telegram.

## What Changes

- **Worker health:** a loopback-only HTTP endpoint in the worker (`/health`) reporting the
  baked revision and whether polling and the database are working; unhealthy while another
  consumer holds the bot.
- **Settings:** secrets read from files (`*_FILE`) and the database URL built from parts,
  as the gateway does; `APP_REVISION` becomes the standard's `AF_REVISION` build argument.
- **Deploy files:** `deploy/release.toml` (images `app` and `backup`; services `db`,
  `migrate`, `worker`, `backup`), `deploy/settings.toml`, `deploy/env/production.env`,
  `deploy/compose.production.yaml` (file secrets, the production gateway's network, the
  backup folder), `deploy/hooks/backup` (a restic snapshot of a database dump before
  migrations), the framework tools in `deploy/af/`, and the release signing public key.
- **CI:** hosted CI from now on (the `default:ci` override ends): checks with a Postgres
  service and `REQUIRE_DB=1`, the gateway contract test against the pinned image, image
  builds for both targets; `release.yml` and `af-release.yml` from the framework.
- **Docs:** deployment guide, `AGENTS.md` (capability `deployed`, the override removed,
  the off-host backup copy recorded as debt).

## Owner decisions (2026-10-20)

- Production grant mirrors development: 30 requests a minute, 2 at a time, USD 5 a
  month, USD 0.05 per call.
- No laptop bot after the move: development uses the tests and conversation scripts on
  the stub model; a laptop worker runs only while the server's is stopped.
- Backups stay on the server at first; an off-host copy is recorded as debt with a
  follow-up change.

## Non-goals

- New assistant features (S3 and later), a web interface, public exposure (nothing is
  published beyond loopback).
- The off-host backup copy (debt, above).
- Zero-downtime switching: a Telegram bot has one poller, so `stop-start` is the only
  correct switch; a release interrupts polling for seconds, and Telegram keeps the updates.

## Capabilities

### New Capabilities

- `release-pipeline`: signed releases, promotion by tag, safe deployment, worker health.

### Modified Capabilities

- `backups`: a backup is also taken before every migration in a deployment.

### Project capabilities and standards

Adds `deployed`: CI on every pull request and `main`, immutable signed releases,
revision-checked health, restore on failure, rollback, deployment evidence, and
operational notices. Cost per message is unchanged (no new model calls).

## Impact

Depends on `decouple-gateway-client` (CI must build without gateway access). Owner
actions: the release signing key (owner-run script), the production grant and the OpenAI
credential for `personal_agent` on the production gateway, approval of the server setup,
the data move, and the Telegram acceptance check.
