## ADDED Requirements

### Requirement: Only allowlisted accounts are answered

The assistant SHALL answer only private-chat messages from Telegram user IDs in
its configured allowlist. It SHALL NOT store or reply to messages from any
other sender or chat, and SHALL log only a count of ignored updates.

#### Scenario: Owner writes

- **WHEN** the allowlisted owner sends a private message
- **THEN** the message is recorded and answered

#### Scenario: Stranger writes

- **WHEN** a Telegram user who is not on the allowlist messages the bot
- **THEN** nothing about the message is stored, no reply is sent, and the
  log shows only that one update was ignored

#### Scenario: Group message

- **WHEN** the bot receives a message from a group chat, even from the owner
- **THEN** it is ignored like a stranger's message

### Requirement: No update is lost across restarts

The assistant SHALL confirm a Telegram update to Telegram only after the
update is durably stored, and SHALL record each update at most once.

#### Scenario: Crash before storing

- **WHEN** the worker stops after receiving an update but before storing it
- **THEN** the update is received again after restart and recorded once

#### Scenario: Update delivered twice

- **WHEN** Telegram delivers the same update ID a second time
- **THEN** it is not recorded again and produces no second reply

### Requirement: Replies feel paced like a person typing

While a reply is being prepared, the assistant SHALL show Telegram's typing
status continuously. It SHALL split replies longer than Telegram's message
limit into ordered messages at paragraph or sentence boundaries, and SHALL
attach a reply reference only when answering a message that is not the most
recent one.

#### Scenario: Slow reply

- **WHEN** preparing a reply takes 12 seconds
- **THEN** the typing status stays visible until the reply arrives

#### Scenario: Long reply

- **WHEN** a reply is 6,000 characters long
- **THEN** it arrives as two or more messages in order, each within the
  limit, split at a paragraph or sentence boundary

### Requirement: Flood control is respected

When Telegram asks the assistant to wait before sending again, the assistant
SHALL wait at least the requested time before retrying, without losing the
reply.

#### Scenario: Telegram rate limit

- **WHEN** sending a reply returns a request to retry after 3 seconds
- **THEN** the reply is sent again no sooner than 3 seconds later and
  arrives once
