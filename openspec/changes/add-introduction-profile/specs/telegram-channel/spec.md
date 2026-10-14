## ADDED Requirements

### Requirement: Action buttons act once

Buttons that confirm, save, cancel, remove, or start something SHALL be
handled by the assistant's code, not answered by the model. Each SHALL act at
most once, and the pressed message SHALL be redrawn with the outcome. A press
whose target has already changed SHALL change nothing and say so briefly.

#### Scenario: Confirm

- **WHEN** the owner presses Confirm on a proposed change
- **THEN** the change is stored once and the message shows "✓ Saved"

#### Scenario: Stale press

- **WHEN** the owner presses Confirm on a change that a newer proposal
  replaced
- **THEN** nothing is stored and the owner is told the proposal is outdated

### Requirement: The bot's name follows the assistant's name

When the assistant's name is saved or a change to it is confirmed, the bot's
Telegram name SHALL be set to it. A failure to rename SHALL NOT undo the
saved name.

#### Scenario: Name saved

- **WHEN** the owner saves "Nara" as the assistant's name
- **THEN** the bot's name in Telegram becomes Nara

#### Scenario: Rename refused

- **WHEN** Telegram refuses the rename
- **THEN** the name stays saved in the profile and the failure is recorded

### Requirement: The command menu lists working commands only

The bot's command menu SHALL list only commands this assistant handles:
`/profile` and `/stop`.

#### Scenario: Menu

- **WHEN** the owner opens the bot's Menu after the worker starts
- **THEN** it shows `/profile` and `/stop` and nothing else
