# Design

## Context

See proposal.md for why. Current state after S1:

- `person` holds only `timezone` (NULL means the fallback, Asia/Jakarta). The
  poller stamps each event with it.
- The reply call has a stable prefix (`persona.md` plus `channels/telegram.md`)
  followed by the recent window, a per-turn developer context note, and the
  owner's turn. One `prompt_cache_key` per person.
- `rich.py` parses output directives (`pa-react`, `pa-effect`, `pa-poll`) and
  quick-reply buttons. A quick-reply press is stored as an owner event and
  answered by the model.
- The outbox sends every Telegram write in order, exactly once except in the
  narrow crash window described in the S1 design.
- Bot API 10.3 (2026-08-24): `setMyName` takes 0-64 characters and applies to
  every user of the bot (optionally per language); `setMyCommands` takes up
  to 100 commands with a scope. The bot currently has about 65 stale
  commands from an earlier setup.

## Goals / Non-Goals

**Goals:** capture the profile with no extra model calls; show the owner
exactly what is stored before it counts; keep the stable prefix cacheable;
make every code-handled button act once even on repeated presses.

**Non-goals:** a structured form outside the chat; per-language bot names;
editing an entry in place from `/profile` (the owner says the new value in
chat, which goes through the normal confirmation).

## Decisions

### 1. The reply model marks profile values with a directive

A reply may contain `<pa-profile field="address">Sam</pa-profile>`. Code
validates and strips it:

- `field` is one of `address`, `assistant_name`, `language`, `reply_style`,
  `timezone`, `work`, `routine`, `interests`;
- the value is plain text up to 300 characters (64 for `assistant_name`,
  the `setMyName` limit);
- `timezone` must be an IANA name that `zoneinfo` loads;
- at most one directive per field per reply; invalid ones are dropped and
  counted like other dropped directives.

During the introduction a valid value becomes a draft. Otherwise it becomes
a pending change, and code appends Confirm and Cancel buttons to the reply.

The guide tells the model to emit the directive only for something the owner
stated directly about themselves or about how the assistant should behave.
This cannot be enforced in code, so it is measured (decision 9).

Rejected:
- **A separate extraction call per turn:** doubles calls on every turn to
  catch a rare event, and a second model could disagree with the reply.
- **Structured output for every reply:** breaks streamed rich text.
- **A tool call:** S1 has no tool loop; S7 adds one, and the directive can
  become a tool then without changing the stored data.

### 2. Profile storage: versioned entries

Table `profile_entry` (migration `0004`): `person_id`, `field`, `value`,
`status` (`draft`, `pending`, `active`, `superseded`), `source_event_ids`,
`turn_id`, `created_at`, `confirmed_at`, `superseded_at`.

- At most one `active` row per person and field (partial unique index), and
  at most one `pending` row per person and field (a newer pending replaces
  the older, which is deleted: it was never confirmed).
- **Confirm or Save:** the current `active` row becomes `superseded` with
  `superseded_at`, and the new row becomes `active` with `confirmed_at`, in
  one transaction.
- **Remove:** deletes every row of that person and field (owner decision).
  The source messages stay in the message log. A `profile_removal` row keeps
  only the field and the time, so while messages that mention the removed
  value are still in the recent window, the context note tells the model not
  to use them.
- **Timezone:** confirming a `timezone` entry also sets `person.timezone` in
  the same transaction, so the poller keeps reading one column; removing it
  sets `person.timezone` back to NULL (the fallback).

Rejected: one JSON column on `person` (no history, no sources); overwriting
values (the owner chose history for changes).

### 3. Introduction state

Table `introduction`: `person_id` (primary key), `state` (`offered`,
`declined`, `active`, `completed`), `offered_at`, `started_at`,
`completed_at`.

- **Offer:** when a turn is answered and the person has no `introduction` row,
  the context note asks the model to add a one-sentence offer after its
  answer, and code appends Start and Later buttons; the row is inserted as
  `offered` with the answer, so the offer happens once.
- **Start** (button or `/start`): state `active`. `/start` when `completed`
  starts a new introduction whose drafts are prefilled from the active
  profile.
- **Later:** state `declined`; never offered again.
- **While `active`:** the context note lists answered topics (with their
  draft values), skipped topics, and the open topics in order. An answer is
  stored only with the reply, so the note says: if the latest message
  answers the first open topic, mark it and ask about the next; otherwise ask
  about the first. For the timezone it asks the model to confirm the current
  one. A skipped topic gets no draft.
- **Summary:** code appends it when the last open topic is answered, after
  any change once all topics are done, or when the model writes
  `<pa-intro-summary/>`; never on the reply to an Edit press, which only
  asks what to change. Finishing early (the model's `<pa-intro-summary/>`
  with topics still open) skips those topics, so Edit afterwards changes an
  answer instead of resuming the questions. The first paid run found this:
  after Edit the model went back to asking about the next topic.
- **Stopping halfway:** drafts stay; normal chat continues with the context
  note offering nothing further; `/start` resumes with the remaining topics.

### 4. The summary and `/profile` are rendered by code

The owner must confirm exactly what is stored, so the summary list is built
from the draft rows, not written by the model: one line per field with an
English label and the owner's value. Buttons: Save and Edit. Edit keeps the
introduction active and asks the model which item to change.

`/profile` is answered without a model call: one line per active entry with
its label, value, and local date set, a Remove button per entry, and a line
saying that changes can be stated in the chat. With no entries it says the
profile is empty and offers `/start`. It is a command event in a turn of its
own (`turn.command`), which no other message joins; the stored assistant
message is a placeholder, so profile values do not stay in the recent window
after a removal. A message from Remove leaves the other Remove buttons of
that list unusable (one action per message); `/profile` again shows a fresh
list.

Rejected: model-written summaries (they can paraphrase or drop a value the
owner then saves unseen).

### 5. Action buttons handled by code

`reply_button` gains `action` (NULL for quick replies) and `target_id`.
Actions: `intro_start`, `intro_later`, `intro_save`, `intro_edit`,
`profile_confirm`, `profile_cancel`, `profile_remove`.

- The press is stored as an event (as today) and the action runs in the same
  transaction, guarded by the button's `pressed_at`, so a repeated press does
  nothing.
- The pressed message is redrawn with the outcome ("✓ Saved", "Cancelled",
  "Removed") as quick replies are today.
- `intro_start` and `intro_edit` also open a turn so the model asks the next
  question; the other actions need no model call.
- A stale action (its target already confirmed, removed, or replaced) is
  acknowledged with a short notice and changes nothing.

Rejected: letting the model answer these presses (it cannot guarantee the
stored outcome).

### 6. Prompt layout

The active profile is rendered after the persona and Telegram guide as an
"About the owner and you" section of the instructions, in a fixed field
order. It changes only on Save, Confirm, or Remove, so the cached prefix is
reused between changes. The persona line "You do not have a name yet" is
replaced by the stored name when there is one. Rules added to the persona:
use the stored address and name, follow the stored language and style
unless the owner's latest message asks otherwise, and never ask about a
field the profile already holds unless the owner brings it up.

Drafts and pending values are not in the instructions; they appear only in
the per-turn context note, so an unconfirmed value never shapes replies.

### 7. Bot name and command menu

- **Name:** Save or a confirmed `assistant_name` writes an outbox row of a
  new kind `bot_name`; the sender calls `setMyName` with no language code.
  A rate-limit waits and retries like other rows; a permanent error is
  recorded on the row and logged, and the profile stays saved. Removing the
  name does not rename the bot back.
- **Commands:** at worker start, `setMyCommands` (default scope) sets
  `profile` and `stop` with short descriptions, only when `getMyCommands`
  differs, so restarts do not repeat the call.

### 8. Data model (migration `0004`)

- `profile_entry` and `introduction` as above, each with `person_id`.
- `reply_button.action`, `reply_button.target_id`, with a check that
  `target_id` is set for actions that need one.
- `outbox.kind` gains `bot_name`; `turn_id` and `message_id` become optional
  for that kind only.
- `profile_removal` (field and time only), `turn.command`, and the
  `command` kind of `inbound_event`.

Forward-only and additive; existing rows are untouched. Rollback is the
previous image: it ignores the new tables and columns.

### 9. Evaluation

Synthetic scripts (free on the stub with scripted replies, then on the real
model, owner-authorised):

- a full introduction in English and one in Indonesian, a skipped topic, a
  stop halfway then `/start` to resume, Edit at the summary, Later;
- explicit changes confirmed and cancelled, a timezone change;
- incidental mentions that must not propose a change ("my friend calls me
  Bob", "reply to him in English");
- a 30-turn conversation after the introduction: zero questions about stored
  fields, replies in the stored language and style (model-judged, owner
  spot-checked).

Pass bar: every structural check passes; zero re-asks; zero false proposals
across the incidental-mention scripts.

## Risks / Trade-offs

- [The model proposes a change the owner did not state] → Confirm is
  required; incidental-mention scripts measure it; the guide gives examples.
- [The model forgets to mark an answer] → the context note lists the
  remaining topics, so it asks again or the summary shows the gap before
  Save.
- [The bot name is bot-wide] → relatives will see the same name (owner
  accepted).
- [Profile text in the instructions is owner-written] → it is the owner's
  own data, rendered as data under a heading, with the 300-character limit.
- [The prefix cache misses after each change] → changes are rare.

## Migration Plan

1. Apply `0004` with the migrate service before the new worker starts.
2. Start the worker: it replaces the command menu and offers the
   introduction on the owner's next message.
3. Rollback: start the previous worker image; the new tables stay unused.
