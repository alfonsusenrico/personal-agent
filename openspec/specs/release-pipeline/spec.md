# release-pipeline Specification

## Purpose
How the assistant reaches production: CI builds and signs each release once, a version tag
promotes it without rebuilding, the server deploys it with a pre-migration backup and
automatic restore, and the worker's health endpoint proves the deployed commit is polling
with its database.

## Requirements

### Requirement: Releases are built once and promoted by tag

Every `main` commit that passes CI SHALL publish one signed release (images, deploy
bundle, record), and a `vX.Y.Z` tag SHALL promote that release to production without
rebuilding.

#### Scenario: Tagged release

- **WHEN** the owner tags a published `main` commit
- **THEN** production's record names the same image digests and the server deploys them

### Requirement: The server deploys the assistant safely

The server SHALL deploy each production record with the standard release engine, back up
the database before any migration, require the worker's health endpoint to report the
record's commit, restore the previous release on failure when the schema is unchanged,
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
