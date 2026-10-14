## ADDED Requirements

### Requirement: 👎 and /note map to reply feedback

The worker SHALL receive the owner's reactions in the private chat. A 👎
added to an assistant message SHALL flag that reply, and a reaction change
that removes 👎 SHALL withdraw the flag; other reactions SHALL be logged only.
`/note <text>` sent as a reply to an assistant message SHALL note that
message, otherwise the latest sent assistant message; `/note` without text
SHALL flag it. `/note` SHALL be acknowledged with a 👌 reaction on the
command message, sent once through the outbox.

#### Scenario: Thumbs down

- **WHEN** the owner reacts 👎 to a reply split into two messages, on the
  second message
- **THEN** the turn's assistant message is flagged once

#### Scenario: Changed mind

- **WHEN** the owner replaces the 👎 with 👍
- **THEN** the flag is withdrawn and the 👍 is logged only

#### Scenario: Note as a reply

- **WHEN** the owner replies to an older answer with "/note too formal"
- **THEN** that answer gets the note and the "/note" message gets 👌

## MODIFIED Requirements

### Requirement: The command menu lists working commands only

The bot's command menu SHALL list only commands this assistant handles:
`/start`, `/profile`, `/note`, and `/stop`.

#### Scenario: Menu

- **WHEN** the owner opens the bot's Menu after the worker starts
- **THEN** it shows `/start`, `/profile`, `/note`, and `/stop` and nothing
  else
