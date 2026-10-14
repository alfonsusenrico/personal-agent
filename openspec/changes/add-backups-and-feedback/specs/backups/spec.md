## ADDED Requirements

### Requirement: The database is backed up daily while the assistant runs

While the backup service runs, it SHALL back up the database when there is
no backup or the newest one is at least 24 hours old, checking on start and
every hour. Only one backup SHALL run at a time.

#### Scenario: First start

- **WHEN** the service starts and the repository has no snapshot
- **THEN** a backup is taken at once

#### Scenario: Laptop asleep overnight

- **WHEN** the laptop wakes and the newest backup is 30 hours old
- **THEN** a backup is taken within the next hourly check

#### Scenario: Recent backup

- **WHEN** the service restarts and the newest backup is 3 hours old
- **THEN** no backup is taken until it is 24 hours old

### Requirement: Backups are encrypted, checked, and kept for a bounded time

Every backup SHALL be encrypted with a password held only in the local
configuration, stored in a local folder outside the repository with owner-only
access, checked for integrity after it is written, and pruned to 7 daily and
4 weekly snapshots.

#### Scenario: Without the password

- **WHEN** someone copies the backup folder without the password
- **THEN** no database content can be read from it

#### Scenario: Retention

- **WHEN** backups have run daily for six weeks
- **THEN** 7 daily and up to 4 weekly snapshots remain

### Requirement: A failed backup never looks like a success

A backup whose database dump fails SHALL leave no snapshot, SHALL be logged
with its error class and exit code only, and SHALL be retried at the next
hourly check. The review SHALL show the age of the newest successful backup.

#### Scenario: Database down

- **WHEN** the database is unreachable during a backup
- **THEN** no new snapshot exists, the log has no content, and the next
  hourly check tries again

### Requirement: A backup restores the same data

A restore check SHALL restore the newest backup into a throwaway database and
compare the applied migrations and each table's row count with the live
database, printing table names and counts only, and SHALL remove the
throwaway database afterwards.

#### Scenario: Unchanged database

- **WHEN** nothing changed since the newest backup
- **THEN** the restore succeeds and every count matches

#### Scenario: Changed since the backup

- **WHEN** new messages arrived after the newest backup
- **THEN** the restore must still succeed with the same migrations, and the
  counts are shown for information

#### Scenario: Broken backup

- **WHEN** the restore fails
- **THEN** the check exits with a failure and the throwaway database is
  removed
