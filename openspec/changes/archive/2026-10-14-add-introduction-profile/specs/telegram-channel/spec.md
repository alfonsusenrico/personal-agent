## ADDED Requirements

### Requirement: Action buttons act once

Buttons that undo or remove something SHALL be handled by the assistant's
code, not answered by the model. Each SHALL act at most once, and the pressed
message SHALL be redrawn with the outcome. A press whose target has already
changed SHALL change nothing and say so briefly.

#### Scenario: Undo

- **WHEN** the owner presses Undo under a saved value
- **THEN** the value is restored once and the message shows "✓ Undone"

#### Scenario: Stale press

- **WHEN** the owner presses Undo under a value that a later message changed
  again
- **THEN** nothing changes and the owner is told the button is outdated

### Requirement: The bot's name follows the assistant's name

When the assistant's name is saved or an Undo restores an earlier one, the
bot's Telegram name SHALL be set to it. A failure to rename SHALL NOT undo the
saved name.

#### Scenario: Name saved

- **WHEN** the owner names the assistant Nara
- **THEN** the bot's name in Telegram becomes Nara

#### Scenario: Rename refused

- **WHEN** Telegram refuses the rename
- **THEN** the name stays saved in the profile and the failure is recorded

### Requirement: The command menu lists working commands only

The bot's command menu SHALL list only commands this assistant handles:
`/start`, `/profile`, and `/stop`.

#### Scenario: Menu

- **WHEN** the owner opens the bot's Menu after the worker starts
- **THEN** it shows `/start`, `/profile`, and `/stop` and nothing else
