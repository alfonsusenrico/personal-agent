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
  to 100 commands with a scope. Bots receive no timezone or location for a
  user; a location arrives only when the user shares it. The bot had about 65
  stale commands from an earlier setup.

## Goals / Non-Goals

**Goals:** learn the profile inside natural conversation with no extra model
calls; never hold the conversation on a question; make every stored value
visible and reversible at once; keep the stable prefix cacheable; make every
code-handled button act once even on repeated presses.

**Non-goals:** a form or wizard of any kind; per-language bot names; editing
an entry in place from `/profile` (the owner says the new value in chat).

## Decisions

### 1. The reply model marks values, declines, and questions

A reply may contain, anywhere (usually at the end):

- `<pa-profile field="address">Sam</pa-profile>`: a value the owner stated;
- `<pa-profile field="routine" skip="true"/>`: the owner declined the topic;
- `<pa-ask field="routine"/>`: this reply asks a getting-to-know question.

Code validates and strips them: `field` is one of `address`,
`assistant_name`, `language`, `reply_style`, `timezone`, `work`, `routine`,
`interests`; a value is plain text up to 300 characters (64 for
`assistant_name`, the `setMyName` limit); `timezone` must be an IANA name
that `zoneinfo` loads; one directive per field per reply; invalid ones are
dropped and counted like other dropped directives.

The persona tells the model to mark only what the owner states directly
about themselves or about how the assistant should behave. This cannot be
enforced in code, so it is measured (decision 9) and every saved value is
shown with Undo.

Rejected:
- **A separate extraction call per turn:** doubles calls on every turn to
  catch a rare event, and a second model could disagree with the reply.
- **Structured output for every reply:** breaks streamed rich text.
- **A tool call:** S1 has no tool loop; S7 adds one, and the directive can
  become a tool then without changing the stored data.

### 2. Profile storage: versioned entries

Table `profile_entry`: `person_id`, `field`, `value`, `status` (`active`,
`superseded`), `source_event_ids`, `turn_id`, `replaces_id`, `saved_at`,
`superseded_at`.

- At most one `active` row per person and field (partial unique index).
- **Save:** the current `active` row becomes `superseded`, and the new row,
  which records it in `replaces_id`, becomes `active`, in one transaction. A
  value equal to the active one saves nothing.
- **Undo:** deletes the rows a reply saved and makes each one's `replaces_id`
  row active again (or leaves the field empty). A row that is no longer
  active makes the press stale.
- **Remove:** deletes every row of that person and field (owner decision).
  The source messages stay in the message log. A `profile_removal` row keeps
  only the field and the time, so while messages that mention the removed
  value are still in the recent window, the context note tells the model not
  to use them.
- **Timezone:** saving, undoing, or removing a `timezone` entry also sets
  `person.timezone` (NULL for none) in the same transaction, so the poller
  keeps reading one column.

Rejected: one JSON column on `person` (no history, no sources); overwriting
values (the owner chose history for changes).

### 3. Pacing getting-to-know questions

Table `profile_topic` (person, field): `asks`, `last_asked_at`,
`declined_at`. Table `profile_pacing` (person): `asking_now_since`, set by
`/start`.

- A topic is **open** when it has no active value, was not declined, and was
  asked fewer than two times.
- The context note lists the open topics only when a question is allowed:
  `/start` asked for questions now, or at least six owner turns have been
  answered since the last question. Otherwise the model is not told what is
  unknown, so it cannot drift into asking.
- The note says: answer the owner first; ask about one open topic only at a
  natural point (small talk, a greeting, or just after finishing a request),
  lightly and optionally; mark it with `<pa-ask/>`; never steer back when
  the owner moves on. For the timezone, confirm the current one rather than
  asking (Telegram does not tell bots a user's timezone or location).
- `<pa-ask>` increments `asks` and sets `last_asked_at`; `skip="true"` sets
  `declined_at`.
- **`/start`:** sets `asking_now_since`, and the note asks the model to ask
  about the next open topic after answering, one per reply. A reply in that
  mode without `<pa-ask>` (the owner moved on, or nothing is open) clears it.

Rejected (live check, 2026-10-14): an introduction mode that asked one topic
per reply in a fixed order and held values until a Save at the end. It made
the conversation feel scripted: every reply steered back to the next topic,
and a stated change during it sat unsaved with no Save in sight. Also
rejected by the owner: a one-time Start/Later offer.

### 4. Saved at once, shown with Undo

When a reply marks values, they are saved in the turn's commit transaction,
and code appends one line per saved value under the reply ("Saved: **What to
call you** → Sam") with one Undo button for that reply. A reply that marks
only a decline or a question shows nothing extra.

Rejected: Confirm per value (a tap for every answer; owner chose Undo) and
silent saving (owner chose to see it).

### 5. Action buttons and `/profile`

`reply_button` gains `action` (NULL for quick replies) and `target_id`.
Actions: `profile_undo` (target: the turn whose values to undo) and
`profile_remove` (target: the active entry).

- The press is stored as an event and the action runs in the same
  transaction, guarded by the button's `pressed_at`, so a repeated press does
  nothing. The pressed message is redrawn with the outcome ("✓ Undone",
  "✓ Removed: Daily routine"); a stale press gets a short notice and changes
  nothing. A stored press also wakes the sender, so a rename goes out at
  once.
- Neither action calls the model.

`/profile` is answered without a model call: one list item per active entry
with its label, value, and local date set, a Remove button per entry, and a
line saying that changes can be stated in the chat. It is a command event in
a turn of its own (`turn.command`), which no other message joins; the stored
assistant message is a placeholder, so profile values do not stay in the
recent window after a removal. One Remove uses up that message's buttons;
`/profile` again shows a fresh list. Values are list items because Rich
Markdown joins single line breaks into one paragraph (seen live).

Rejected: letting the model answer these presses (it cannot guarantee the
stored outcome).

### 6. Prompt layout

The active profile is rendered after the persona and Telegram guide as "The
owner's profile" section of the instructions, in a fixed field order. It
changes only when a value is saved, undone, or removed, so the cached prefix
is reused between changes. The persona uses the stored name (without one, it
says the owner can give it one), follows the stored language and style
unless the owner's latest message asks otherwise, and never asks about a
stored field unless the owner brings it up.

### 7. Bot name and command menu

- **Name:** saving `assistant_name`, or an Undo that restores one, writes an
  outbox row of kind `bot_name`; the sender calls `setMyName` with no
  language code. A rate limit waits and retries like other rows; a permanent
  error is recorded on the row and logged, and the profile stays saved.
  Removing the name does not rename the bot.
- **Commands:** at worker start, `setMyCommands` (default scope) sets
  `start`, `profile`, and `stop`, only when `getMyCommands` differs, so
  restarts do not repeat the call.

### 8. Data model (migration `0004`)

- `profile_entry`, `profile_removal`, `profile_topic`, `profile_pacing`,
  each with `person_id`.
- `reply_button.action` and `target_id` (both set or both NULL).
- The `command` kind of `inbound_event` and `turn.command`.
- `outbox.kind` gains `bot_name`; `turn_id` and `message_id` are optional for
  that kind only.

Forward-only and additive; existing rows are untouched. Rollback is the
previous image: it ignores the new tables and columns. The first version of
this change was applied only to the owner's local database, which was moved
to this schema by hand, keeping its saved values.

### 9. Evaluation

Synthetic scripts (free on the stub with scripted replies, then on the real
model, owner-authorised):

- a first conversation that gets to know the owner gradually while they talk
  about other things;
- a topic jump right after a question, and a declined topic;
- `/start` answered for a few topics, then an unrelated request;
- stated changes (name, style, timezone), Undo, and the bot's name;
- incidental mentions that must not save anything ("my friend calls me
  Bob");
- a 30-turn conversation with a saved profile: no questions about stored
  fields.

Pass bar: every structural check passes; no question about a stored field;
no reply that ignores a topic jump; nothing saved from incidental mentions;
no more than one question per six turns outside `/start`.

## Risks / Trade-offs

- [The model saves something the owner did not state] → it is shown at once
  with Undo; incidental-mention scripts measure it.
- [The model asks at an awkward moment] → code allows at most one question
  every six turns and two per topic; the note only lists open topics when a
  question is allowed.
- [The model forgets to mark a question] → pacing then undercounts; the
  scripts count questions in transcripts.
- [The bot name is bot-wide] → relatives will see the same name (owner
  accepted).
- [Profile text in the instructions is owner-written] → it is the owner's
  own data, rendered as data under a heading, with the 300-character limit.

## Migration Plan

1. Apply `0004` with the migrate service before the new worker starts.
2. Start the worker: it replaces the command menu.
3. Rollback: start the previous worker image; the new tables stay unused.
