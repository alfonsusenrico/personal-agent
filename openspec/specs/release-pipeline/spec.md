# release-pipeline Specification

## Purpose
How the assistant reaches production: CI builds and signs each release once, a version tag
promotes it without rebuilding, the server deploys it with a pre-migration backup and
automatic restore, and the worker's health endpoint proves the deployed commit is polling
with its database.

## Requirements

### Requirement: Releases are built once and promoted by tag

`pakem release promote vX.Y.Z --production` SHALL send a request for one `main` commit,
signed with the releasing device's own key, and that commit's source to the server, which
SHALL build the images once and deploy them; a commit whose CI checks did not pass SHALL
be refused, and the commit SHALL be tagged `vX.Y.Z` only after it deployed.

#### Scenario: Tagged release

- **WHEN** the owner promotes a checked `main` commit as `vX.Y.Z`
- **THEN** the server runs images built from exactly that commit, `/health` reports it,
  and the tag `vX.Y.Z` names it

#### Scenario: Unchecked commit

- **WHEN** the commit's CI checks failed or are still running
- **THEN** the release is refused and production does not change

### Requirement: The server deploys the assistant safely

The server SHALL deploy each release with the standard release engine, back up the
database before any migration, require the worker's health endpoint to report the
release's commit, restore the previous release on failure when the schema is unchanged,
and notify the owner of every result.

#### Scenario: Failed health check

- **WHEN** a new release's worker does not report its commit
- **THEN** the previous release runs again and the owner is notified on Telegram

#### Scenario: Release with a migration

- **WHEN** a release contains a new migration
- **THEN** a backup of the database is written and checked before the migration runs, and
  a failed backup stops the deployment with nothing migrated

#### Scenario: Conversation survives a release

- **WHEN** the owner writes during a release
- **THEN** the message is answered once after the new worker starts, with the earlier
  conversation intact

### Requirement: The worker reports whether it is working

The worker SHALL serve a loopback-only health endpoint reporting its revision, and SHALL
report unhealthy when polling has not succeeded for 90 seconds, when another consumer holds
the bot, or when the database does not answer. It SHALL report no message content or
identifiers other than the revision.

#### Scenario: Another worker takes the bot

- **WHEN** a second worker starts polling the same bot
- **THEN** the health endpoint reports unhealthy and the server's monitor notifies the owner

#### Scenario: Healthy

- **WHEN** polling and the database work
- **THEN** the endpoint answers 200 with the running commit
