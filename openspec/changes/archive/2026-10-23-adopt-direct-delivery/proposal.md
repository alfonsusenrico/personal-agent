# Proposal

## Why

Releases publish signed images to GHCR from GitHub Actions and a pull agent on the server
deploys the tagged record (agent-framework ADR 0009). The owner's Pakem ADR 0010
(accepted 2026-10-21) replaces that for personal projects with direct delivery: the
owner's device sends signed source of one commit to the server, which builds and deploys
it, with no registry or hosted release jobs. The owner approved building it and adopting
this project with `pakem` as Proof 1 of ADR 0011 (2026-10-22).

## Expected Outcome

- `pakem release promote vX.Y.Z --production` deploys an image the server built from
  exactly that commit, only when its CI checks passed, and tags the commit after it runs.
- `pakem release rollback previous --production` returns to the release that ran before,
  without rebuilding; `pakem release history` lists every release.
- The deployment itself is unchanged: pre-migration backup, health that proves the commit,
  restore on failure, a Telegram notice for every result.

## What Changes

- `.pakem/` (facts and decisions, from `pakem project adopt`); CI runs on every push to
  `main`, tag, and pull request; `[build] runner = "local"` in `deploy/release.toml`.
- Removed: `release.yml`, `af-release.yml`, `deploy/af/af_publish.py`,
  `deploy/release-signing.pub` (`deploy/af/contracts.py` stays for `scripts/check`).
- Docs: `docs/deployment.md`, AGENTS.md, CHANGELOG.

## Non-goals

- No change to the worker, the database, settings, or backups.
- The CI contract test keeps the gateway image it pins (provisional decision
  `contract.image`).

## Capabilities

No new capability.
