# Tasks

## 1. Data model and settings

- [ ] 1.1 Add migrations `0001` (person, identity, conversation,
  inbound_event, turn, message) and `0002` (outbox, model_call), with
  `person_id` on every table, UTC times plus timezone, trust and source links,
  and uniqueness on `(channel, external_update_id)`. Verify with migration
  tests on an empty schema (applies, re-applies as a no-op, constraints hold).
- [ ] 1.2 Add settings: the bot token and gateway key (secrets), allowlist,
  gateway URL, fallback timezone, debounce, window budget, and offline
  threshold. Update `.env.example`. Verify with settings tests: missing
  secrets name the variable, values are never echoed.

Expected Result: the schema and configuration exist and are tested; nothing
runs yet.

## 2. Telegram channel

- [ ] 2.1 Add the Bot API client (`getUpdates`, `sendMessage`,
  `sendChatAction`) with `retry_after` handling and token-free logging.
  Verify with mocked-transport tests, including a 429 with `retry_after` and a
  check that no log line contains the token.
- [ ] 2.2 Add the poller: allowlist and private-chat filter, envelope mapping
  (message, edit, unsupported; forward origin to `owner_forwarded`), store,
  then confirm. Verify with tests: stranger and group ignored and not stored,
  duplicate update ignored, crash before commit redelivers, edits stored as new
  events.
- [ ] 2.3 Add reply splitting and typing refresh. Verify with tests: a
  6,000-character reply splits at boundaries under the limit, and typing is
  refreshed while a slow reply is generated.

Expected Result: Telegram updates are stored exactly once and replies render
correctly.

## 3. Conversation core

- [ ] 3.1 Add turn assembly: debounce merge, one active turn per person,
  merging a mid-generation message with one regeneration, and edit handling
  (before or after the answer). Verify with tests using a fake gateway and a
  controllable clock for every scenario in the `conversation` spec.
- [ ] 3.2 Add context building: the persona file, a token-budgeted recent
  window, and the per-turn context message (time, timezone, offline delay,
  edit notice, unreadable content, forwarded content quoted as untrusted).
  Verify with tests on the built input: order, budget, cache key, and that the
  date is never in `instructions`.
- [ ] 3.3 Add the gateway calls through `llm-gateway-client` (pinned by
  commit), with the call ID scheme, `execution_unknown` retried once,
  fallback apology, budget notice, and content-free `model_call` records.
  Verify with tests for each outcome against a fake client, plus one contract
  test against the gateway stub when `GATEWAY_TEST_URL` is set.
- [ ] 3.4 Add the outbox and sender: an atomic answer commit, sending,
  restart recovery (pending, sending, and unanswered turns). Verify with
  restart tests: exactly one reply after a stop at each stage.

Expected Result: every conversation scenario passes against a fake gateway.

## 4. Worker, compose, and docs

- [ ] 4.1 Add `personal-agent-worker` (poller, turn runner, sender, advisory
  lock), the compose `worker` service, and content-free logging of turns.
  Verify with `docker compose up --build` on the local stack: health in the
  logs, and a second worker refused by the lock.
- [ ] 4.2 Add `scripts/converse` and synthetic conversation scripts
  (`tests/conversations/`), with transcripts written to the git-ignored
  `research/eval/`. Verify with a free run against the gateway stub.
- [ ] 4.3 Update `README.md` (setup: bot, gateway registration, allowlist)
  and the `AGENTS.md` architecture map. Verify that the documented setup works
  on the local stack.

Expected Result: the assistant runs locally end to end on the stub.

## 5. Live verification (owner-authorised; needs the bot token and gateway keys)

- [ ] 5.1 Run `./scripts/check`. Then, on Telegram with gpt-6-luna:
  - a burst merge, a mid-turn follow-up, edits before and after the answer,
    a voice note and a photo with a caption, a forwarded message, a restart
    while a reply is pending, and an offline period over the threshold;
  - English, Indonesian, and mixed messages.

  Record results, cost per turn, and log scans in `verification.md`.
- [ ] 5.2 Run the synthetic scripts on the `reply` profile at low and medium
  effort (owner-authorised, under USD 0.50), compare quality and cost for the
  owner, and record the chosen default.

## Workflow follow-up

- Report the branch and the verification record; the owner decides merge,
  archive, and when the assistant moves to always-on hosting.
