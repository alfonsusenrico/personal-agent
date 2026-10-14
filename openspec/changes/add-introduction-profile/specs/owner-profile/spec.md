## Purpose

What the assistant knows about how the owner wants to be treated (names,
language, reply style, timezone, and a few basics), how it is learned in an
introduction and through confirmed changes, and how it shapes every reply.

## ADDED Requirements

### Requirement: The introduction is offered once

The assistant SHALL offer the introduction once, after answering the owner's
first message when no introduction has been offered, with Start and Later
buttons. After Later it SHALL NOT offer it again. The owner SHALL be able to
start or resume it at any time with `/start`.

#### Scenario: First message after the change

- **WHEN** the owner writes "morning!" and no introduction was offered before
- **THEN** the assistant answers and offers the introduction with Start and
  Later buttons

#### Scenario: Later

- **WHEN** the owner presses Later
- **THEN** the introduction is not offered again, and `/start` still begins it

### Requirement: The introduction asks one topic at a time

The introduction SHALL cover what to call the owner, the assistant's name,
language, reply style, work or study, daily routine, interests, and a
confirmation of the timezone, asking one question at a time. Every question
SHALL be skippable, and a stopped introduction SHALL resume with its
remaining topics.

#### Scenario: Skipped topic

- **WHEN** the owner answers "skip" to the interests question
- **THEN** the assistant moves to the next topic and stores nothing for
  interests

#### Scenario: Resume

- **WHEN** the owner stops after three answers and later sends `/start`
- **THEN** the introduction continues with the remaining topics and keeps the
  three answers

### Requirement: Nothing from the introduction counts before Save

The introduction SHALL end with a summary of exactly the values that will be
stored, with Save and Edit buttons. Its values SHALL NOT shape replies or be
stored as active until the owner presses Save.

#### Scenario: Save

- **WHEN** the owner presses Save on the summary
- **THEN** every listed value becomes active and later replies use them

#### Scenario: Edit

- **WHEN** the owner presses Edit and says "call me Sam instead"
- **THEN** a new summary lists Sam, and nothing is active until Save

### Requirement: Stated changes need one confirmation

Outside the introduction, a change the owner states directly SHALL be offered
with Confirm and Cancel buttons and stored only on Confirm. The assistant
SHALL NOT propose a change from something the owner did not state about
themselves or about how it should behave.

#### Scenario: Confirmed change

- **WHEN** the owner writes "call me Sam from now on" and presses Confirm
- **THEN** the address becomes Sam and replies use it

#### Scenario: Incidental mention

- **WHEN** the owner writes "my friend calls me Bob"
- **THEN** no change is proposed

#### Scenario: Timezone change

- **WHEN** the owner writes "I'm in Tokyo this week" and confirms
  Asia/Tokyo
- **THEN** times in replies and stamps on new messages use Asia/Tokyo

### Requirement: Changes keep history and removals forget

Confirming a new value SHALL keep the previous value as dated history.
Removing an entry SHALL delete it and every earlier version of it; the raw
messages it came from SHALL stay in the message log.

#### Scenario: Change

- **WHEN** the address changes from Samuel to Sam
- **THEN** Sam is active and Samuel is kept with the date it was replaced

#### Scenario: Remove

- **WHEN** the owner removes the routine entry
- **THEN** no current or earlier routine value remains, and replies stop
  using it

### Requirement: The owner can see the profile

`/profile` SHALL list every active entry with its value and the date it was
set, with a Remove button per entry, without a model call. Pressing a button
again or after its entry changed SHALL change nothing.

#### Scenario: View

- **WHEN** the owner sends `/profile`
- **THEN** each stored entry is listed with its date and a Remove button

#### Scenario: Repeated press

- **WHEN** the owner presses Remove twice on the same entry
- **THEN** the entry is removed once and the second press changes nothing

### Requirement: Replies follow the profile

Replies SHALL use the stored address and assistant name and follow the
stored language and reply style unless the owner's latest message asks
otherwise, and SHALL NOT ask about a stored field unless the owner brings it
up.

#### Scenario: Known preference

- **WHEN** the profile says to reply briefly and casually in English, and the
  owner asks a question
- **THEN** the reply is brief, casual, in English, and does not ask how they
  like replies

#### Scenario: Owner asks otherwise

- **WHEN** the stored language is English and the owner writes in Indonesian
- **THEN** the reply is in Indonesian

### Requirement: The profile stays private

Profile values SHALL never appear in logs, and SHALL be shown only in the
owner's own chat.

#### Scenario: Logs

- **WHEN** an entry is saved, changed, or removed
- **THEN** the log records the field and the action, never the value
