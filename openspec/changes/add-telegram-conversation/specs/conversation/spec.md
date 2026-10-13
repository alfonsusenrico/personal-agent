## ADDED Requirements

### Requirement: Bursts of messages are answered as one turn

The assistant SHALL treat messages the owner sends in quick succession, with
no more than the configured quiet period between them, as one turn answered
by one reply. A message that arrives while a reply is being prepared and
before any part of it is committed for sending SHALL join that turn, and the
reply SHALL be prepared again with it at most once per turn. Messages
arriving later SHALL start the next turn.

#### Scenario: Three quick messages

- **WHEN** the owner sends three messages one second apart
- **THEN** the assistant sends one reply that addresses all three

#### Scenario: Follow-up while thinking

- **WHEN** the owner sends another message while the reply is still being
  prepared and nothing has been sent
- **THEN** the reply is prepared again including the new message, and only
  that reply is sent

#### Scenario: Messages while already answering

- **WHEN** a further message arrives after the regenerated reply has started
- **THEN** it is answered in the next turn

### Requirement: Every turn is answered exactly once

The assistant SHALL answer every recorded turn, including turns left
unanswered when the worker stopped, and SHALL NOT send a reply twice except
when the worker stops in the instant between Telegram accepting a message and
the assistant recording that it was sent.

#### Scenario: Restart mid-turn

- **WHEN** the worker stops after recording the owner's message but before
  replying, and then restarts
- **THEN** the owner receives exactly one reply to that message

#### Scenario: Restart after the reply was prepared

- **WHEN** the worker stops after preparing a reply but before sending it
- **THEN** the prepared reply is sent after restart, without a new model call

### Requirement: Replies follow the owner's language and the current time

Each reply SHALL be written in the language of the owner's latest message,
and the assistant SHALL know the current date, time, and timezone (the
configured fallback timezone until a profile exists) when answering.

#### Scenario: Indonesian message

- **WHEN** the owner writes in Indonesian
- **THEN** the reply is in Indonesian

#### Scenario: Asking the day

- **WHEN** the owner asks what day it is
- **THEN** the reply gives the correct date in the owner's timezone

### Requirement: Offline delays are acknowledged

When the owner's oldest unanswered message in a turn has waited longer than
the configured offline threshold, the reply SHALL briefly acknowledge the delay
and still answer the messages.

#### Scenario: Assistant was offline overnight

- **WHEN** the owner sent a message at 23:00 and the assistant comes back
  online at 07:30
- **THEN** the reply briefly mentions it was unavailable and answers the
  message

#### Scenario: Normal pace

- **WHEN** the owner's message is answered within the threshold
- **THEN** the reply does not mention any delay

### Requirement: Edited messages are answered again

When the owner edits a message, the assistant SHALL use the edited text if
the turn containing it has not been answered, and SHALL otherwise answer the
edit as a new turn that knows what was changed.

#### Scenario: Edit before the reply

- **WHEN** the owner fixes a typo before the assistant replies
- **THEN** the reply is based on the corrected text, and only one reply is
  sent

#### Scenario: Edit after the reply

- **WHEN** the owner changes "meet at 3" to "meet at 4" after the assistant
  replied
- **THEN** the assistant sends a new reply that responds to the change

### Requirement: Unreadable messages get an honest reply

When the owner sends a photo, voice note, sticker, file, or other non-text
message, the assistant SHALL record it and reply briefly, in the owner's
language, that it cannot read that kind of message yet, using any caption or
accompanying text to answer what it can.

#### Scenario: Voice note

- **WHEN** the owner sends only a voice note
- **THEN** the assistant replies that it cannot listen to voice notes yet

#### Scenario: Photo with a question

- **WHEN** the owner sends a photo with a caption asking a question
- **THEN** the reply says it cannot see the photo and answers what it can
  from the caption

### Requirement: Model calls are idempotent, bounded, and costed

Every model call SHALL go through the owner's gateway with this project's key
and a stable call identifier, so a resubmission never pays twice. A call whose
outcome is unknown SHALL be retried at most once under a new identifier. If no
reply can be produced, the assistant SHALL send a short fixed apology, and a
fixed notice when the monthly budget is exhausted. Each call's tokens, cost,
latency, and outcome SHALL be recorded without any message content.

#### Scenario: Gateway lost the response

- **WHEN** a reply call ends with an unknown outcome and the retry succeeds
- **THEN** the owner gets one reply, and both calls are recorded with their
  costs

#### Scenario: Budget used up

- **WHEN** the gateway refuses a call because the monthly budget is
  exhausted
- **THEN** the owner receives a short notice saying the budget is used up,
  and no further model call is made for that turn

#### Scenario: Cost visible

- **WHEN** the owner checks the recorded calls for a day
- **THEN** each turn's calls show tokens and cost, and no message text
