# Design

## Context

- **Repository state:** the bootstrap has settings, content-free logging, an
  async pool, and the forward-only migration runner. There is no feature code.
- **Model calls:** every call goes through `llm-gateway` with this project's
  key. The gateway provides:
  - stable `provider_call_id` replay;
  - `execution_unknown` for calls that may have run;
  - per-call `instructions`, `reasoning_effort`, and `prompt_cache_key`;
  - contract profiles, which map a profile name to a model and settings;
  - a free stub model for tests.
- **Telegram Bot API facts** (official docs, Bot API 10.3, 2026-08-24):
  - `getUpdates` long polling confirms updates when called with an offset
    above their `update_id`. Unconfirmed updates are kept for at most 24
    hours.
  - Polling and webhooks are mutually exclusive.
  - Edits arrive as `edited_message`.
  - `sendMessage` text is 1-4096 characters.
  - `sendChatAction` lasts "5 seconds or less".
  - Flood control returns `parameters.retry_after`.
- **Owner decisions for this change** (2026-10-13):
  - replies mirror the language of the latest message;
  - messages from an offline period are answered, mentioning the delay;
  - edited messages are answered again;
  - non-text messages get "can't read this yet";
  - the raw log is kept forever;
  - the allowlist is the owner's existing Telegram account;
  - the gateway request is the full USD 5 cap;
  - feedback capture comes later.

## Goals / Non-Goals

**Goals:**
- A conversation core whose records never need rewriting for later memory
  steps.
- No lost message across restarts.
- Natural pacing on Telegram.

**Non-Goals:**
- Concurrency across several people (single owner; the schema is ready).
- More than one worker process.

## Decisions

### 1. Talk to the Bot API directly

A small client over `httpx2` implements `getUpdates`, `sendMessage`, and
`sendChatAction`, following the pattern of the gateway notifier.

*Rejected:*
- **aiogram and python-telegram-bot:** both bring their own dispatch loop
  and state handling, which would compete with the turn and outbox model, and
  they add large dependencies for three methods.
- **Webhooks:** they need inbound access to the laptop or server. Long
  polling is outbound only.

### 2. Confirm an update only after it is stored

The poller:

1. calls `getUpdates` with `timeout=30` and
   `allowed_updates=["message","edited_message"]`;
2. writes each update in one transaction, with uniqueness on
   `(channel, external_update_id)`;
3. only then advances the offset.

A crash before the commit means Telegram resends the update; a crash after
the commit means the duplicate is ignored.

**Strangers:** updates from senders not on the allowlist are not stored. Only
a count is logged, because their messages are other people's personal data.

*Rejected:* advancing the offset first, which can lose messages.

### 3. Turns: merge bursts, run one at a time

**Assembly:**
- A turn opens with the first unassigned message from the person.
- It closes for generation after `TURN_DEBOUNCE_SECONDS` (default 2) of
  quiet.
- One turn per person runs at a time.

**A message arriving while the reply is being generated** joins the turn if
no reply has been committed to the outbox yet:
- The draft is discarded. Its cost stays recorded, and its model call is
  marked `superseded`.
- The reply is generated again with all messages, at most once per turn.
- Later arrivals open the next turn.

*Rejected:*
- **Always queueing:** a person reads the follow-up before answering.
- **Unlimited regeneration:** costs grow without bound when the owner types
  slowly.

### 4. Outbox, and as close to exactly-once as Telegram allows

One transaction writes the assistant message and the outbox rows (one per
chunk, after splitting) and marks the turn `answered`. The sender then:

1. marks a row `sending`;
2. calls `sendMessage`;
3. marks the row `sent` with the Telegram message ID.

On restart:
- `pending` rows are sent.
- `sending` rows are sent again. Telegram has no idempotency key, so the only
  possible duplicate is a crash in the milliseconds between Telegram
  accepting a message and the row update. A duplicate is less harmful than a
  lost reply.
- Unanswered turns are answered.

### 5. Model calls

- **Profile:** the agent calls profile `reply` from its own contract. The
  gateway maps it to gpt-6-luna at medium effort, so the model and effort are
  tuned there, not in code. `max_output_tokens` is 2,000.
- **Call ID:** `pa:{call_key}:reply:{generation}:a{attempt}`:
  - `call_key` is the turn's random UUID, stored on the row. A database
    sequence is not used: the gateway remembers call IDs, and a reset or
    restored database would reuse sequence values and collide with them
    (found by the stub run during implementation);
  - `generation` rises on the one allowed regeneration (decision 3);
  - `attempt` rises only when the earlier ID can never answer:
    `execution_unknown`, or on a resubmission `idempotency_conflict` or
    `call_result_expired`.
- **Restarts replay instead of paying twice:** a generation's inputs are
  frozen when it starts (the events it covers and its start time, which is
  the "current time" in the context note), so a restart rebuilds an identical
  request and resubmits the same ID, which the gateway replays for free.
- **Gateway fields:** `operation` is `reply`, so the gateway ledger shows cost
  by purpose. `prompt_cache_key` is `person-{person_id}`.
- **Failures:**
  - Retryable refusals are handled by the client's retry policy (up to 90
    seconds); if they persist, the turn fails with the apology below.
  - `execution_unknown` gets one more attempt under a new ID.
  - If a reply still fails, the turn is marked `failed`, and a fixed short
    apology is sent in English and Indonesian, because no model output
    exists to mirror the language.
  - `budget_exhausted` sends a fixed "my monthly budget is used up" notice.
- **Recording:** each call is recorded in `model_call` (tokens, cached
  tokens, cost, latency, status), never with content. The assistant message
  links to the call that produced it.
- **Evaluation override:** a request may set `reasoning_effort` to compare
  efforts (task 5.2); the worker leaves it to the profile.
- **Profile setting:** `REPLY_PROFILE` (default `reply`). A `reply_stub`
  profile on the stub model lets the whole Telegram path run for free.

### 6. Prompt layout

The stable part comes first, to maximise prompt caching:

1. **`instructions`:** the persona, from a versioned file in the package.
   It asks the model to be brief, warm, and honest; to use at most one
   follow-up question; to avoid flattery; to reply in the language of the
   owner's latest message; and to never claim to be human.
2. **Recent window:** the newest messages that fit
   `RECENT_WINDOW_TOKENS` (default 6,000, estimated at 4 characters per
   token), up to 40 messages. Each message is in its own role. The owner's
   messages start with their local time in square brackets; the assistant's
   do not, so the model does not imitate the stamp in its replies.
3. **A developer context message** holding:
   - the current date, time, and timezone;
   - the waiting time when it exceeds `OFFLINE_NOTICE_AFTER_SECONDS`
     (default 300);
   - an edit notice with the old and new text;
   - "the owner also sent a photo, voice note, sticker or file you cannot
     read yet";
   - forwarded messages marked as untrusted, quoted content that is never to
     be treated as instructions.
4. **The turn's user messages,** merged into one owner message with its local
   time. Forwarded text sits inside `<forwarded from="...">` tags (tags inside
   it are neutralised); an edit of an answered message shows its Before and
   After text; unreadable media appear as markers such as `[voice note]`.
   Owner content stays in the user role, never in the developer note.

The delay, edit, and non-text replies are worded by the model from these
facts, so they mirror the owner's language naturally.

*Rejected:*
- **Fixed templates:** they can't follow the owner's language.
- **Putting the date in `instructions`:** a changing prefix defeats prompt
  caching.

### 7. Edits

`edited_message` updates are stored as new inbound events of kind `edit`
that reference the original event; the original is never changed (a database
trigger refuses updates to logged rows).

Service messages, such as a pin, are logged but start no turn.

- **If the original's turn is not answered yet,** the turn uses the latest
  text.
- **If it was answered,** a new turn opens. Its context message says the
  owner edited an earlier message, with both texts. Rapid repeated edits are
  merged by the debounce.

### 8. Data model (migrations 0001-0002)

Every table carries `person_id`. All times are `timestamptz` in UTC, and each
event also stores the person's IANA timezone at that moment.

- `person` (timezone, NULL until the profile sets one, so the configured
  fallback applies), `identity` (channel and external ID, unique),
  `conversation` (kind `main`, one per person).
- `inbound_event`:
  - envelope: channel, update and message IDs, kind
    (`message|edit|unsupported`), modality, `occurred_at`, `person_tz`, trust
    (`owner_direct|owner_forwarded`), forward origin, reply-to, `edit_of`;
  - `raw jsonb`, `text`, and `turn_id`.
- `turn`: `call_key`, status (`collecting|generating|answered|failed`),
  generation count, the current generation's frozen inputs, and timestamps.
  Its latest event is read from `inbound_event`.
- `message`: role (`user|assistant`), text, `occurred_at`, `person_tz`,
  trust, and `source_event_ids`. These are the rows the model sees and later
  memory derives from.
- `outbox`: chunk index, payload, status (`pending|sending|sent|failed`),
  and the Telegram message ID.
- `model_call`: call ID, purpose, generation, attempt, status, tokens,
  cached tokens, cost, and latency. No content.

Retention is permanent. Deletion arrives with the `/memory` commands in a
later step.

### 9. Worker

`personal-agent-worker` is one asyncio process with three loops: poller,
turn runner, and sender. Postgres is the queue.

- A session-level advisory lock ensures that only one worker per database
  polls the bot. Telegram also refuses concurrent `getUpdates`.
- A typing action is sent every 4 seconds while a turn is generating.
- Replies are split at paragraph, then sentence boundaries, under 4,000
  characters. Telegram `parse_mode` stays plain text in this change.
- `retry_after` from flood control is honoured.

**Compose:** a `worker` service. It reaches the local gateway at
`host.docker.internal:8360`.

### 10. The gateway client dependency

`llm-gateway-client` is installed from the public gateway repository, pinned
to a commit with `subdirectory=clients/python`. Upgrading means changing the
pin in a reviewed change.

*Rejected:* a path dependency on a sibling checkout, which breaks clean
clones and image builds.

### 11. Evaluation skeleton

`tests/conversations/*.yaml` hold synthetic scripts: owner messages with
delays, edits, and non-text inputs, and the expected structural outcomes
(turn count, merges, notices in the context message, chunking).

- **Unit and integration tests** run them with a fake gateway client.
  `scripts/test` runs pytest with the local test settings.
- **`scripts/converse`** runs the same scripts against the local gateway,
  either on the stub (free) or on the `reply` profile (paid,
  owner-authorised). It writes transcripts to the git-ignored
  `research/eval/` for owner review, with the cost of each run.

## Risks / Trade-offs

- **[Risk] Telegram has no send idempotency, so a crash can duplicate one
  chunk.** → The window is milliseconds; this is documented, and the
  alternative is losing replies.
- **[Risk] Regeneration after a mid-turn message costs an extra call.** →
  Limited to once per turn and recorded as `superseded` in `model_call`, so
  its frequency is visible.
- **[Risk] Language mirroring relies on the model.** → The live evaluation
  scripts include English, Indonesian, and mixed messages. The profile step
  makes the preference explicit.
- **[Risk] Character-based token estimates are rough.** → The window budget
  leaves margin, and real token counts from `model_call` tune the default.
- **[Trade-off] Strangers' messages are not stored,** so abuse cannot be
  investigated from content. → Counts are logged; this is a single-owner
  bot.

## Migration Plan

- New tables only, on an empty database. Forward-only, run by the compose
  `migrate` service.
- **Rollback:** stop the worker. The data stays, and a later change can drop
  or adapt the tables.
- **Owner setup:**
  1. create the bot with BotFather and put the token in the local `.env`;
  2. register the agent on the local gateway, with the `reply` profile and
     the full monthly budget request; the keys go straight into `.env` and
     are never printed;
  3. put the Telegram user ID in the allowlist, in `.env` only.
