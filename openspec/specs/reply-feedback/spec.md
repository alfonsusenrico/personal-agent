# reply-feedback Specification

## Purpose
How the owner marks a bad reply without changing the conversation: a flag or a note recorded on the assistant's message in a channel-neutral form, each channel mapping its own gesture to it, reviewed locally and closed as a regression test or dismissed.

## Requirements

### Requirement: A reply can be flagged without changing the conversation

The owner SHALL be able to flag an assistant reply as bad through the
channel's own gesture. A flag SHALL be recorded against the assistant's
message with the event that created it, SHALL make no model call, SHALL NOT
enter the conversation history, and SHALL NOT count as an owner turn.
Withdrawing the gesture SHALL withdraw the flag.

#### Scenario: Flag a reply

- **WHEN** the owner flags the assistant's reply about boiling eggs
- **THEN** one open `dislike` exists for that assistant message, no model
  call is made, and the next reply's history does not mention it

#### Scenario: Withdraw a flag

- **WHEN** the owner withdraws that gesture
- **THEN** the flag is marked withdrawn and no longer listed as open

#### Scenario: Flag the owner's own message

- **WHEN** the owner applies the gesture to their own message
- **THEN** the event is logged and no flag is created

### Requirement: A note says what was wrong

The owner SHALL be able to attach a note to an assistant reply: the reply
they point at, or else the latest sent reply. A note with no text SHALL be a
plain flag. The note SHALL be acknowledged without a model call.

#### Scenario: Note on a pointed reply

- **WHEN** the owner points at an older reply and notes "too long"
- **THEN** a `note` with "too long" exists for that reply and the owner sees
  an acknowledgement

#### Scenario: Note right after a reply

- **WHEN** the owner notes "wrong timezone" without pointing at a reply
- **THEN** the note is attached to the latest sent assistant reply

#### Scenario: Empty note

- **WHEN** the owner sends the note gesture with no text
- **THEN** a `dislike` exists for the latest sent assistant reply

### Requirement: Feedback is channel-neutral

A flag or note SHALL reference the assistant's message in the conversation
record, not a channel's message. Only the source event SHALL carry the
channel and its raw gesture.

#### Scenario: Stored shape

- **WHEN** a flag is created from a Telegram reaction
- **THEN** the flag references the assistant message row, and the channel
  appears only on its source event

### Requirement: Flags are reviewed locally

A local review command SHALL list open flags oldest first with their time,
the owner's message before the reply, the reply, and the note, plus the age
of the newest backup. It SHALL close a flag with the outcome `test` or
`dismissed`, and SHALL offer a summary form with counts and backup age only.

#### Scenario: Review

- **WHEN** the owner runs the review with two open flags
- **THEN** both are listed with their context, and the newest backup's age is
  shown

#### Scenario: Close

- **WHEN** the owner closes a flag as `test`
- **THEN** it is no longer open and keeps its outcome and closing time

#### Scenario: Summary

- **WHEN** the summary form runs
- **THEN** it prints counts and backup age and no message text

### Requirement: Notes stay private

Logs SHALL hold flag identifiers, kinds, and counts only, never a note or
message text.

#### Scenario: Logged note

- **WHEN** the owner notes "you forgot my sister's name"
- **THEN** the log line names the flag kind and identifiers and contains no
  note text
