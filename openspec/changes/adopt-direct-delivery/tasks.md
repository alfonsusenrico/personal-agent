# Tasks

## 1. Repository

- [x] 1.1 `pakem project adopt`: `.pakem/project.toml` and gap decisions from the
  repository's evidence. Verify: `pakem project check --gaps` all settled.
- [x] 1.2 Remove the registry release workflows, `af_publish.py`, and the signing key; CI
  on push, tags, and pull requests; `[build] runner = "local"`; docs. Verify:
  `./scripts/check`; actionlint.

Expected Result: the repository releases only through `pakem release`.

## 2. Server and first release (owner-approved steps)

- [x] 2.1 Owner, from the terminal: `pakem setup host` (engine update), `pakem host
  allow-device --switch-from-registry`. Verify: `pakem release status` answers. (Done
  2026-10-22 as one command, `pakem setup host msi`, with release port 2223.)
- [x] 2.2 Owner-instructed `pakem release promote v0.2.0 --production`. Verify: deployment
  record `deployed`, `/health` revision, a Telegram reply, tag pushed. (2026-10-23.)
- [ ] 2.3 Owner: delete the repository secrets `COSIGN_PRIVATE_KEY` and `COSIGN_PASSWORD`.

Expected Result: production runs a direct release; the owner merges and the change is archived.
