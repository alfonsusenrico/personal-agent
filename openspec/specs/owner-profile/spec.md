# owner-profile Specification

## Purpose
What the assistant knows about how the owner wants to be treated (names,
language, reply style, timezone, and a few basics), how it learns it
gradually inside normal conversation, and how it shapes every reply.

## Requirements

### Requirement: Getting to know the owner never interrupts the conversation

Every reply SHALL first respond to what the owner said and follow a change of
topic. The assistant MAY ask about one thing it does not know yet, only at a
natural point and only when pacing allows: at most one such question every
six owner turns, at most two attempts per topic, and none for a topic the
owner declined.

#### Scenario: Topic jump

- **WHEN** the assistant asked about the owner's routine and the owner replies
  "how long should I boil an egg?"
- **THEN** the reply answers the egg question and does not return to the
  routine

#### Scenario: Declined topic

- **WHEN** the owner answers a question about their routine with "not now"
- **THEN** the assistant does not ask about the routine again unless the owner
  brings it up

#### Scenario: Pacing

- **WHEN** the assistant asked a getting-to-know question in the owner's
  previous turn
- **THEN** the next five replies ask no getting-to-know question

### Requirement: /start asks the open questions now

`/start` SHALL make the assistant ask about the topics it does not know yet,
one per reply, while the owner keeps answering. When the owner moves to
something else, the assistant SHALL follow them and stop asking.

#### Scenario: Asking now

- **WHEN** the owner sends `/start` and the routine and interests are unknown
- **THEN** the reply asks about one of them, and the next reply asks about the
  other if the owner answered

#### Scenario: Moving on

- **WHEN** the owner answers a `/start` question with an unrelated request
- **THEN** the assistant handles the request and asks nothing further

### Requirement: What the owner states is saved at once with Undo

A value the owner states directly about themselves or about how the assistant
should behave SHALL be saved immediately and shown under the reply as the
stored value with an Undo button. Undo SHALL restore the previous value, or
none. The assistant SHALL NOT save what others call the owner, requests about
other people, or guesses.

#### Scenario: Stated name

- **WHEN** the owner writes "call me Sam from now on"
- **THEN** the reply uses Sam, shows "What to call you → Sam" with Undo, and
  later replies use Sam

#### Scenario: Undo

- **WHEN** the owner presses Undo under "What to call you → Sam" and the
  earlier value was Samuel
- **THEN** the stored value is Samuel again

#### Scenario: Incidental mention

- **WHEN** the owner writes "my friend calls me Bob"
- **THEN** nothing is saved

#### Scenario: Timezone change

- **WHEN** the owner writes "I'm in Tokyo this week"
- **THEN** Asia/Tokyo is saved with Undo, and times in replies and stamps on
  new messages use it

### Requirement: Changes keep history and removals forget

Saving a new value SHALL keep the previous value as dated history. Removing an
entry SHALL delete it and every earlier version of it; the raw messages it
came from SHALL stay in the message log.

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

- **WHEN** an entry is saved, undone, or removed
- **THEN** the log records the field and the action, never the value
