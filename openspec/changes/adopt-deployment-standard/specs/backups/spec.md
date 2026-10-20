## ADDED Requirements

### Requirement: A deployment backs up before it migrates

A deployment whose release contains a migration SHALL first write an encrypted backup of
the whole database, check it, and stop the deployment if either step fails.

#### Scenario: Backup fails

- **WHEN** the pre-migration backup cannot be written or does not pass its check
- **THEN** no migration runs, the deployment stops with the database as it was, and the
  owner is notified

#### Scenario: Restore from the pre-migration backup

- **WHEN** the owner restores the pre-migration backup into a throwaway database
- **THEN** the restore check finds the same tables and row counts as before the release
