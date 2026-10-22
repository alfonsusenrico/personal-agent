# Deployment

The agent is released with `pakem release` (Pakem ADR 0010, direct delivery, on the
deployment contracts v1): the owner's device signs a request for the exact commit and
sends its source to the server, which builds both images once and deploys them with the
standard release engine. The laptop keeps the code, the tests, and the stub conversation
scripts; only one worker may poll the bot.

## Releases

1. Every pull request, push to `main`, and `v*` tag runs `ci.yml`: `./scripts/check` with
   a Postgres service (database tests required), the contract test against the pinned
   gateway image, and builds of both images. `pakem release` refuses a commit whose checks
   did not pass.
2. `pakem release promote vX.Y.Z --production` (the project has no staging; the version
   should match `pyproject.toml`) sends the current `main` commit to the server, which
   builds the `app` image (the Dockerfile's `runtime` stage: migrate and worker) and the
   `backup` image (`restic` and a PostgreSQL 16 client) for its own architecture, then
   deploys:
   - the old worker stops (30 seconds to finish; a reply in progress is finished after the
     restart, never paid twice), so polling pauses for seconds and Telegram keeps the updates;
   - a release with migrations first writes a pre-migration backup (below) and stops if it
     fails;
   - migrations run, the new worker starts, and `/health` must report the commit; otherwise
     the previous release is restored (same schema) or everything stays stopped for the owner
     (schema changed);
   - every result is sent to the owner on Telegram.

   The commit is tagged `vX.Y.Z` only after it deployed.
3. `pakem release rollback previous --production` returns to the release that ran before,
   from the builds the server keeps; `pakem release history` lists every release.

`deploy/af/contracts.py` is the framework's contract check; update it from a framework
release.

## Settings

`deploy/settings.toml` names every runtime setting and its one source:

- **Defaults** (`deploy/env/production.env`, committed): environment, log level, database
  name and user, the gateway URL on its network (`http://api:8000`), the reply profile, the
  fallback timezone, and the conversation tunables.
- **Placement** (`<base>/placement.env` on the host, 0600): `TELEGRAM_ALLOWED_USER_IDS`,
  `BACKUP_DIR` (the daily backups' folder), `AGENT_HEALTH_PORT` (the worker's loopback health
  port), and `AGENT_GATEWAY_NETWORK` (the production gateway's Compose network).
- **Secrets** (`<base>/secrets/<NAME>`, 0640, set with `af_engine.py secret-set` from standard
  input and never printed): `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `GATEWAY_API_KEY` (the
  `personal_agent` project's production key), `RESTIC_PASSWORD`.

Containers read secrets as files (`<NAME>_FILE`), and the database URL is built from
`DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER`, and `DATABASE_PASSWORD_FILE`, so no URL
with a password is written anywhere. The host is `personal-agent-db`, a name only this project uses:
the worker also joins the gateway's network, where `db` is the gateway's database. A secret file open to others, or writable by its group,
is refused at startup.

## Host setup (owner)

From the owner's terminal, with the owner's own admin login:

1. `pakem setup host <ssh-target> --name <host>` (with the flags the server was set up
   with) installs or updates the engine and the direct delivery receiver.
2. `pakem host allow-device <host> --project-dir <this repository>` lets this device's key
   release the agent: it writes the direct host config and the base
   `<deploy root>/personal-agent/production/` (owned by the deploy user, 0700), and names
   any missing placement or secret settings. A server that ran the registry release passes
   `--switch-from-registry` once; its placement, secrets, and backups stay.
3. `placement.env` as above, and `BACKUP_DIR` created for the container user: owned by uid
   10001, mode 0700.
4. The four secrets. Keep a copy of `RESTIC_PASSWORD` in a password manager: without it no
   backup can be read.

## The gateway (owner)

On the production gateway, with its admin command:

1. `project upgrade-contract personal_agent`, so the profiles name catalogue aliases.
2. `project set-request personal_agent --env production`: 30 requests a minute, 2 at a time,
   USD 5 a month, and USD 0.30 per call. The production gateway reserves each reply's worst
   case before it runs (about USD 0.27 for `openai-gpt-6-luna`; the gateway's paid
   accounting guide) and charges the real cost after, so a lower per-call ceiling refuses
   every reply with `cost_ceiling_exceeded`. Development runs on the laptop's gateway, so
   the development request on the production gateway stays at zero; the gateway shares
   one monthly pool between projects, and `grants show` shows what was granted.
3. Register the project's OpenAI key with the gateway's write-only credential command.
4. Issue the production key and pipe it straight into `secret-set GATEWAY_API_KEY`.

## Moving the data from the laptop

One bot, one poller, one database. The engine's first deployment has no previous release, so
it writes no pre-migration backup; the laptop's final backup is the fallback.

1. The laptop worker stays stopped; stop the laptop's `backup` service after it has written a
   final snapshot (`./scripts/review --summary` shows its age).
2. Before the first deployment, create the production database volume under the Compose
   project's name and labels, start a one-off `postgres:16-alpine` on it with the
   `POSTGRES_PASSWORD` secret, and stream a custom-format dump of the laptop database into
   `pg_restore --single-transaction --exit-on-error --no-owner` there (over SSH, so no
   unencrypted copy is left on either disk). Compare every table's row count and a content
   checksum on both sides; only hashes and counts are printed. Remove the one-off container
   (the volume stays).
3. Release the first version. The migration step finds the schema current; the worker answers what
   Telegram kept while nothing polled.
4. Send a message, check `/profile`, and flag a reply with 👎.
5. Keep the laptop database stopped for two weeks as a fallback, then remove it with the
   owner's approval. Moving back is the same in reverse: stop the server's worker, dump,
   restore on the laptop.

Turn call keys are random and stored on the rows, so the moved data never reuses a call ID
the gateway remembers.

## Health

The worker serves `GET /health` on `127.0.0.1:${AGENT_HEALTH_PORT}`:
`{"status", "revision", "polling", "database"}`, 200 when a poll stored its updates within
the last 90 seconds, no other consumer holds the bot, and the database answers; 503
otherwise. The release engine requires it to report the release's commit after each start,
and the server's monitor checks it every few minutes and notifies `unhealthy` and
`recovered`. If another program polls the same bot, Telegram answers it with 409 and the
endpoint turns unhealthy within one poll.

## Backups

- **Daily:** the `backup` service runs on the server as on the laptop, into `BACKUP_DIR`
  (7 daily and 4 weekly snapshots, each checked after it is written).
- **Before migrations:** `deploy/hooks/backup` writes a new restic repository in
  `<base>/backups/<time>/` with one snapshot of the whole database (`pg_dump` streamed into
  `restic`, so nothing unencrypted touches the disk) and checks it by reading all its data.
  A failure stops the deployment before any migration; the previous release's data is
  unchanged and the owner is notified.
- **Restore** (it replaces the current data): stop the worker, then pipe
  `restic dump --host personal-agent latest /personal_agent.dump` from the chosen repository
  into `pg_restore --clean --if-exists --no-owner --exit-on-error` in the `backup` container,
  then start the worker.
- **Known debt:** every copy is on the server's disk, so a disk loss loses everything after
  the last off-server copy. A follow-up change adds a restic copy to a second location after
  two weeks in production.
