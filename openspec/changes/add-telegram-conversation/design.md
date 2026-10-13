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
  - Rich messages (Bot API 10.1-10.3, 2026): `sendRichMessage` takes Rich
    Markdown that "is compatible with GitHub Flavored Markdown where possible"
    (headings, lists, task lists, tables, code, quotes, `<details>`,
    footnotes, LaTeX, spoilers, `tg://time` stamps, `<tg-button>` rows). Limits:
    32,768 UTF-8 characters, 500 blocks, 16 nesting levels, 50 media, 20
    table columns. Media blocks take HTTP(S) URLs only.
  - `sendRichMessageDraft` and `sendMessageDraft` (private chats) show a
    "temporary 30-second preview"; an empty draft or `<tg-thinking>` shows a
    placeholder; `can_stop` adds a Stop button that sends a
    `stopped_message_generation` update with the `draft_id`.
  - Callback buttons carry 1-64 bytes of data and arrive as
    `callback_query`; `answerCallbackQuery` ends the button's spinner;
    `editMessageText` accepts `rich_message`.
  - `setMessageReaction` accepts 73 listed emoji; `message_effect_id` works
    in private chats, but effect IDs are not published for bots.
  - Votes in non-anonymous polls the bot sent arrive as `poll_answer`.
- **The gateway finishes a streamed call after the client disconnects** to
  settle its exact cost (gateway spec `llm-calls`).
- **Owner decisions for this change** (2026-10-13):
  - replies mirror the language of the latest message;
  - messages from an offline period are answered, mentioning the delay;
  - edited messages are answered again;
  - non-text messages get "can't read this yet";
  - the raw log is kept forever;
  - the allowlist is the owner's existing Telegram account;
  - the gateway request is the full USD 5 cap;
  - feedback capture comes later.
- **Owner decisions on output** (2026-10-13, after the plain-text version was
  verified): the agent knows the platform it writes to and uses Telegram's
  complete output features from the start; each later platform gets complete
  support when it is added. Stream a live draft with a Stop button; Stop
  discards the reply; a message during a visible draft still makes it
  rethink once; quick-reply, link, and copy buttons, and a pressed quick reply
  shows the choice and removes the buttons; reactions, message effects,
  native polls, and images by URL, with URLs limited to ones already in the
  chat.

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

A small client over `httpx2` implements the methods used (`getUpdates`,
`sendRichMessage`, `sendRichMessageDraft`, `sendMessage`, `editMessageText`,
`answerCallbackQuery`, `setMessageReaction`, `sendPoll`, `getMe`), following
the pattern of the gateway notifier. `allowed_updates` adds `callback_query`,
`poll_answer`, and `stopped_message_generation`.

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

1. **`instructions`:** the persona, from a versioned file in the package,
   followed by the Telegram guide (decision 12). The persona asks the model
   to be brief, warm, and honest; to use at most one follow-up question; to
   avoid flattery; to reply in the language of the owner's latest message;
   and to never claim to be human.
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

### 8. Data model (migrations 0001-0003)

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
- `0003` adds Telegram output: event kinds `button`, `poll_answer`, and
  `stop` (a message ID is optional for them); turn status `stopped` and the
  current `draft_id`; model-call status `stopped`; outbox `kind` and JSON
  `payload` (text optional for reactions); `reply_button` (token, label,
  pressed time) and `reply_poll` (Telegram poll ID, question, options).

Retention is permanent. Deletion arrives with the `/memory` commands in a
later step.

### 9. Worker

`personal-agent-worker` is one asyncio process with three loops: poller,
turn runner, and sender. Postgres is the queue.

- A session-level advisory lock ensures that only one worker per database
  polls the bot. Telegram also refuses concurrent `getUpdates`.
- A live draft replaces the typing action (decision 15).
- Rich replies are split only above Telegram's rich limits (rare at 2,000
  output tokens); the plain-text fallback splits at paragraph, then sentence
  boundaries, under 4,000 characters.
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

### 12. The model writes Telegram's own format

The instructions are the persona plus a Telegram guide
(`conversation/channels/telegram.md`), both stable and so cached. The guide
teaches when to format (casual chat stays short plain text; structure only
for structured content), the Rich Markdown syntax, buttons, the output
directives below, and the limits. The model's output is Telegram Rich
Markdown, sent with `sendRichMessage`.

*Rejected:*
- **Regular messages with MarkdownV2 or HTML:** 4,096 characters, strict
  escaping (one stray character rejects the message), no headings or tables.
- **A neutral format converted per platform:** an extra layer that loses the
  platform's own features; the owner wants each platform complete.

### 13. Output directives

Telegram actions that are not message text are written as the agent's own
tags (prefix `pa-`, so they never clash with Telegram's `tg-` tags). The
sender removes them from the text and performs them:

- `<pa-react emoji="👍"/>`: `setMessageReaction` on the owner's latest
  message in the turn, only with one of Telegram's 73 allowed emoji. A reply
  that is only a reaction sends no message.
- `<pa-effect name="celebrate"/>`: a message effect from a fixed map
  (celebrate, love, like, fire, dislike, poop). The IDs are the commonly used
  ones and are verified live; a send rejected for its effect is retried
  without it.
- `<pa-poll question="..." multiple="false"><option>...</option></pa-poll>`:
  a non-anonymous `sendPoll` after the message (1-12 options, question up to
  300 characters), so the owner's vote comes back.

Invalid directives are dropped and counted in the logs. Drafts never show
directives.

### 14. Buttons

- The model writes `<tg-button-row>` rows with `callback_data` (quick
  replies), `url`, and `copy_text` buttons. Other button types (Mini Apps,
  login, inline switches) are removed.
- Each quick reply's data is replaced by a random token (`b:` and 16 hex
  characters) stored with its label in `reply_button`, so a press maps to a
  known button even after a database reset, and the model never chooses
  callback data.
- A press arrives as `callback_query`: allowlist check, `answerCallbackQuery`
  to end the spinner, then an inbound event of kind `button` whose text is
  the label. It joins turns like a typed message. The message is then edited
  to replace its quick-reply rows with "✓ label" (owner decision). A token
  already used, or unknown, gets a short "no longer available" notice and
  nothing else.

### 15. Streaming drafts

- Each generation streams through the gateway (`responses.stream`). The
  first draft is `<tg-thinking>` with `can_stop`; then the text so far is
  sent with `sendRichMessageDraft` at most once per
  `DRAFT_INTERVAL_SECONDS` (default 1), only when it changed, with
  unfinished tags and directives hidden. Drafts are refreshed at least every
  20 seconds, because they last 30.
- Each generation gets a random `draft_id`, stored on the turn, so a Stop
  press maps to its turn.
- The final message still goes through the outbox (exactly once); Telegram
  removes the draft when the bot sends. A regeneration starts a new draft,
  replacing the visible one (owner decision). A replayed call after a restart
  arrives whole, without visible streaming.
- Draft errors are cosmetic: logged by code and ignored.

*Rejected:* typing and the full message (the owner chose streaming);
sending a message and editing it repeatedly (leaves "edited" marks and costs
more requests).

### 16. Stop

`stopped_message_generation` for the draft of a generating turn cancels its
stream. The turn becomes `stopped`, its model call is recorded as `stopped`,
and nothing is sent (owner decision). The owner's message is kept, followed
by an assistant record "[stopped by the owner before the reply was
finished]", so the next reply knows. The gateway still completes and charges
the call. A Stop for an old or unknown draft is ignored.

### 17. Images by URL

A media block is kept only if its exact URL appeared in an owner message in
the turn or the recent window (later also in tool results); other media
blocks are removed before sending (owner decision). Media URLs are never
fetched by the agent; Telegram fetches them.

### 18. Sending and fallback

- Outbox rows have a kind (`rich`, `plain`, `reaction`, `poll`) and a JSON
  payload.
- When Telegram rejects a rich message with 400, the sender sends the same
  reply once as plain text with the markup removed and records
  `fallback_plain`. Whether every client renders rich messages is checked on
  the owner's devices during the live check.
- The stored assistant message is the reply as sent, directives included, so
  the model sees what it did.

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
- **[Risk] Rich messages are new (June 2026) and client support is not
  documented.** → Plain-text fallback on rejection; the live check covers
  the owner's phone and desktop.
- **[Risk] Effect IDs are not published for bots.** → A fixed, live-verified
  map; a rejected effect is retried without it.
- **[Trade-off] Stop does not save cost,** because the gateway settles the
  full call. → Documented; cancellation would be a gateway change.
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
