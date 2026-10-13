# Tasks

## 1. Data model and settings

- [x] 1.1 Add migrations `0001` (person, identity, conversation,
  inbound_event, turn, message) and `0002` (outbox, model_call), with
  `person_id` on every table, UTC times plus timezone, trust and source links,
  and uniqueness on `(channel, external_update_id)`. Verify with migration
  tests on an empty schema (applies, re-applies as a no-op, constraints hold).
- [x] 1.2 Add settings: the bot token and gateway key (secrets), allowlist,
  gateway URL, fallback timezone, debounce, window budget, and offline
  threshold. Update `.env.example`. Verify with settings tests: missing
  secrets name the variable, values are never echoed.

Expected Result: the schema and configuration exist and are tested; nothing
runs yet.

## 2. Telegram channel

- [x] 2.1 Add the Bot API client (`getUpdates`, `sendMessage`,
  `sendChatAction`) with `retry_after` handling and token-free logging.
  Verify with mocked-transport tests, including a 429 with `retry_after` and a
  check that no log line contains the token.
- [x] 2.2 Add the poller: allowlist and private-chat filter, envelope mapping
  (message, edit, unsupported; forward origin to `owner_forwarded`), store,
  then confirm. Verify with tests: stranger and group ignored and not stored,
  duplicate update ignored, crash before commit redelivers, edits stored as new
  events.
- [x] 2.3 Add reply splitting and typing refresh. Verify with tests: a
  6,000-character reply splits at boundaries under the limit, and typing is
  refreshed while a slow reply is generated.

Expected Result: Telegram updates are stored exactly once and replies render
correctly.

## 3. Conversation core

- [x] 3.1 Add turn assembly: debounce merge, one active turn per person,
  merging a mid-generation message with one regeneration, and edit handling
  (before or after the answer). Verify with tests using a fake gateway and a
  controllable clock for every scenario in the `conversation` spec.
- [x] 3.2 Add context building: the persona file, a token-budgeted recent
  window, and the per-turn context message (time, timezone, offline delay,
  edit notice, unreadable content, forwarded content quoted as untrusted).
  Verify with tests on the built input: order, budget, cache key, and that the
  date is never in `instructions`.
- [x] 3.3 Add the gateway calls through `llm-gateway-client` (pinned by
  commit), with the call ID scheme, `execution_unknown` retried once,
  fallback apology, budget notice, and content-free `model_call` records.
  Verify with tests for each outcome against a fake client, plus one contract
  test against the gateway stub when `GATEWAY_TEST_URL` is set.
- [x] 3.4 Add the outbox and sender: an atomic answer commit, sending,
  restart recovery (pending, sending, and unanswered turns). Verify with
  restart tests: exactly one reply after a stop at each stage.

Expected Result: every conversation scenario passes against a fake gateway.

## 4. Worker, compose, and docs

- [x] 4.1 Add `personal-agent-worker` (poller, turn runner, sender, advisory
  lock), the compose `worker` service, and content-free logging of turns.
  Verify with `docker compose up --build` on the local stack: health in the
  logs, and a second worker refused by the lock.
- [x] 4.2 Add `scripts/converse` and synthetic conversation scripts
  (`tests/conversations/`), with transcripts written to the git-ignored
  `research/eval/`. Verify with a free run against the gateway stub.
- [x] 4.3 Update `README.md` (setup: bot, gateway registration, allowlist)
  and the `AGENTS.md` architecture map. Verify that the documented setup works
  on the local stack.

Expected Result: the assistant runs locally end to end on the stub.

## 5. Telegram output

- [x] 5.1 Add the Telegram guide (`conversation/channels/telegram.md`) to the
  instructions and move formatting rules out of the persona. Verify with
  context tests: the instructions hold the persona and the guide, with no
  date.
- [x] 5.2 Add migration `0003` (event kinds, turn `stopped` and `draft_id`,
  model-call `stopped`, outbox kind and payload, `reply_button`,
  `reply_poll`). Verify with migration and constraint tests.
- [x] 5.3 Add reply rendering: directive parsing (reaction, effect, poll),
  button filtering and token rewriting, media URL filtering, rich limits and
  splitting, and the plain-text conversion for fallback. Verify with unit
  tests for each rule, including invalid directives and invented URLs.
- [x] 5.4 Extend the Bot API client (`sendRichMessage`,
  `sendRichMessageDraft`, `editMessageText`, `answerCallbackQuery`,
  `setMessageReaction`, `sendPoll`) and the sender (kinds, plain fallback,
  effect retry). Verify with mocked-transport tests: rich body shape, 400
  then plain fallback, effect rejected then retried without it.
- [x] 5.5 Accept `callback_query`, `poll_answer`, and
  `stopped_message_generation`: allowlist, button and poll events that join
  turns, the "✓ label" edit, used-token notice, and Stop. Verify with flow
  tests for each spec scenario.
- [x] 5.6 Stream generations through the gateway with drafts (placeholder,
  pacing, refresh, hidden markup, new draft on regeneration, Stop
  cancellation). Verify with flow tests using a streaming fake gateway and a
  controllable clock, and the gateway contract test on the stub.
- [x] 5.7 Update the conversation scripts with formatting, buttons, poll,
  reaction, and Stop cases; update `README.md` and `AGENTS.md`. Verify with
  a free stub run.

Expected Result: every Telegram output scenario passes against the fakes and
the gateway stub.

## 6. Live verification (owner-authorised; needs the bot token and gateway keys)

- [ ] 6.1 Run `./scripts/check`. Then, on Telegram with gpt-6-luna, on the
  owner's phone and desktop:
  - a burst merge, a follow-up while a draft is visible, edits before and
    after the answer, a voice note and a photo with a caption, a forwarded
    message, a restart while a reply is pending, and an offline period over
    the threshold;
  - English, Indonesian, and mixed messages;
  - a formatted answer (table, list, code, formula), a quick-reply press and
    a repeated press, a poll vote, a reaction, each message effect, an image
    URL from the chat and an invented one, Stop on a streaming reply.

  Record results, cost per turn, and log scans in `verification.md`.
- [x] 6.2 Run the synthetic scripts on the `reply` profile at low and medium
  effort (owner-authorised, under USD 0.50), compare quality and cost for the
  owner, and record the chosen default. Done on the plain-text version.
- [x] 6.3 Rerun 6.2 with the Telegram guide (under USD 0.50) and compare
  formatting choices (plain for chat, structure only when it helps).

## Workflow follow-up

- Report the branch and the verification record; the owner decides merge,
  archive, and when the assistant moves to always-on hosting.
