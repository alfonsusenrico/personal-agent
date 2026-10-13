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

### Requirement: Replies stream as a live draft

While a reply is being prepared, the assistant SHALL show it as a Telegram
draft in the private chat: a thinking placeholder first, then the text written
so far, refreshed before the draft expires and offering a Stop button. Draft
updates SHALL never show directives or unfinished markup, and a failed draft
update SHALL never delay or change the final reply.

#### Scenario: Slow reply

- **WHEN** preparing a reply takes 40 seconds
- **THEN** a draft stays visible the whole time, first as a placeholder and
  then with the text so far, and the final message replaces it

#### Scenario: Draft update rejected

- **WHEN** Telegram rejects a draft update
- **THEN** the final reply is still sent once, unchanged

### Requirement: Replies point at older messages only when needed

The final reply SHALL attach a reply reference only when answering a message
that is not the owner's most recent one.

#### Scenario: Owner moved on

- **WHEN** the owner sent a new message while an older one was being answered
- **THEN** the older one's reply quotes the message it answers

### Requirement: Replies use Telegram's rich formatting

Replies SHALL be sent as Telegram rich messages written in Telegram Rich
Markdown, using structure only where it helps. A reply Telegram rejects as a
rich message SHALL be sent once as plain text with the markup removed, split
under the plain-message limit, and recorded as a fallback. Replies longer than
Telegram's rich-message limits SHALL be split in order at block boundaries.

#### Scenario: Comparison request

- **WHEN** the owner asks for a comparison of three phone plans
- **THEN** the reply may arrive as one rich message with a table

#### Scenario: Casual chat

- **WHEN** the owner says "good morning"
- **THEN** the reply is short plain text without headings or lists

#### Scenario: Rich message rejected

- **WHEN** Telegram rejects a rich reply with an error
- **THEN** the same reply arrives as plain text, once

### Requirement: Quick replies come back as answers

The assistant SHALL offer quick-reply, link, and copy-text buttons only, and
SHALL replace each quick reply's callback data with a random token it stores.
A press from the allowlisted owner on a known, unused token SHALL be
acknowledged, recorded as the owner's answer with the button's label, answered
like a typed message, and SHALL turn the message's quick replies into a mark of
the chosen option. Presses on unknown or used tokens, and presses from anyone
else, SHALL not produce an answer.

#### Scenario: Owner picks an option

- **WHEN** the owner presses "Indonesian" under a question
- **THEN** the buttons are replaced by "✓ Indonesian" and the assistant
  answers as if the owner had typed "Indonesian"

#### Scenario: Old button pressed again

- **WHEN** the owner presses a quick reply that was already used
- **THEN** Telegram shows a short notice and no turn is started

### Requirement: Reactions, effects, polls, and images are used safely

The assistant SHALL be able to react to the owner's latest message with an
emoji Telegram allows, add a message effect from a fixed set, ask a native
non-anonymous poll whose vote returns as the owner's answer, and embed images
or files by URL. A media URL SHALL be embedded only if it already appeared in
an owner message in the turn or the recent window; other media SHALL be
removed. Invalid directives SHALL be dropped, and a send rejected for its
effect SHALL be retried without the effect.

#### Scenario: Thanks

- **WHEN** the owner writes "thanks, got it"
- **THEN** the assistant may react with 👍 and send no message

#### Scenario: Invented image

- **WHEN** a reply embeds an image URL that never appeared in the chat
- **THEN** the image is removed and the rest of the reply is sent

#### Scenario: Poll vote

- **WHEN** the owner votes in a poll the assistant sent
- **THEN** the vote is recorded as the owner's answer and answered

### Requirement: Stop discards the reply

When the owner presses Stop on the draft of a reply being prepared, the
assistant SHALL stop preparing it and send nothing for that turn, and SHALL
record that the owner stopped it. A Stop for any other draft SHALL be ignored.

#### Scenario: Owner stops a long answer

- **WHEN** the owner presses Stop while a reply is streaming
- **THEN** the draft disappears, no message is sent, and the next reply
  knows the previous one was stopped

### Requirement: Flood control is respected

When Telegram asks the assistant to wait before sending again, the assistant
SHALL wait at least the requested time before retrying, without losing the
reply.

#### Scenario: Telegram rate limit

- **WHEN** sending a reply returns a request to retry after 3 seconds
- **THEN** the reply is sent again no sooner than 3 seconds later and
  arrives once
