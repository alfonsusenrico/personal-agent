# Verification: add-telegram-conversation

Status: the plain-text version and the Telegram output (tasks 5.1-5.7, 6.3)
are verified locally. The live Telegram check with the owner (6.1) is pending.

Branch `feat/telegram-conversation`, working tree on top of `main` at
`7ef1199`, 2026-10-13. Local only: macOS host, Docker Compose, Postgres 16
(`127.0.0.1:54331`), the local llm-gateway (`127.0.0.1:8360`, client pinned
at `505f6e1`).

## Data

Synthetic only. The tests and conversation scripts are invented messages; no
real conversation was used. The startup check used the owner's real bot
token and gateway key, read from the local `.env` and never printed.

## Results

| Check | Command | Result |
|---|---|---|
| Lock, format, lint, types | `./scripts/check` | passed; pyright 0 errors |
| Tests | `./scripts/check` (`REQUIRE_DB=1`) | 92 passed, 0 skipped |
| Compose | `docker compose config -q` | passed |
| OpenSpec | `openspec validate --all --strict` | 1 passed |
| Image | `docker compose build`; persona loaded from the image | passed (2,152 characters) |
| Migrations | `docker compose up --build migrate` on the local database | 0001 and 0002 applied |
| Worker startup | `REPLY_PROFILE=reply_stub docker compose up -d worker` | logs: Telegram bot ready, gateway project `personal_agent` (development), worker started |
| Second worker | `docker compose run --rm worker` while one runs | "Another worker holds the worker lock; exiting" |
| Gateway contract | `tests/test_gateway_contract.py` against the local gateway stub | passed: completed, replayed on resubmission, `idempotency_conflict` on a changed body |
| Conversation scripts, free | `./scripts/converse` (16 scripts, stub) | 16 ok, USD 0 |
| Conversation scripts, paid | `./scripts/converse --profile reply --paid --effort medium` and `--effort low` | 16 ok each; USD 0.002155 and 0.001756 |

Spec coverage (tests named in `tests/`):

- **telegram-channel:** owner answered, stranger and group not stored and only
  counted (`test_stranger_and_group_messages_are_not_stored`); crash before
  storing redelivers (`test_crash_before_storing_redelivers_the_update`);
  duplicate delivery recorded once; 12-second typing (`test_typing.py`);
  6,000-character split (`test_render.py`, `test_long_reply_arrives_in_ordered_chunks`);
  `retry_after` 3 s honoured, sent once (`test_flood_control_waits_and_sends_once`).
- **conversation:** three quick messages, follow-up while thinking (one
  superseded call), message after regeneration started, restart mid-turn,
  restart after the reply was prepared (no new call), restart mid-generation
  (same call ID replayed), the owner's date and time, offline overnight and
  normal pace, edits before and after the reply, voice note, photo with
  caption, `execution_unknown` then success, two unknown outcomes (apology),
  budget exhausted (notice, one call), cost recorded without content.
- **message-log:** event fields and trust, edit as a new event, immutability
  trigger, forwarded text quoted as untrusted, merged-burst source links,
  content-free logs (`test_logs_hold_no_message_text_or_token`).

## Defect found and fixed during verification

The first free stub run failed 4 of 16 scripts with `attempts_exhausted`.
Call IDs were built from the turn's database ID (`pa:1:reply:1:a1`); each
fresh database reused it, and the gateway, which remembers call IDs, refused
the different body with `idempotency_conflict`. A reset or restored
production database would have hit the same failure. Each turn now has a
random `call_key` (UUID) that names its calls. Regression test:
`test_a_reset_database_never_reuses_a_call_id`; the rerun passed 16 of 16.
The design and `AGENTS.md` record the rule.

## Effort comparison (task 6.2, plain-text version)

Same 16 scripts, 19 calls each, gpt-6-luna through the `reply` profile:

| Effort | Median latency | Max | Mean cost per call | Mean output / reasoning tokens |
|---|---|---|---|---|
| medium | 2.7 s | 6.5 s | USD 0.000113 | 111 / 50 |
| low | 2.0 s | 7.2 s | USD 0.000092 | 69 / 15 |

- Both followed the language of the latest message (English, Indonesian, a
  switch), gave the correct date and days left in the month, refused to
  pretend to set a reminder, said they could not see last month's messages,
  apologised for the overnight delay, declined to judge the mushroom photo,
  and treated the forwarded injection as content.
- Medium gave the fuller detailed answer (a year-by-year table, a comparison
  with simple interest); low was shorter and assumed rupiah.
- Both read "let's meet at 3 at the cafe" as an invitation to the assistant
  ("I can't meet in person"). The owner judged the script unrealistic (a plan
  is phrased as a request, such as "schedule me a meeting at 3 at X with N"),
  so the script was changed, not the persona. Rerun at medium (USD
  0.000258): it said it cannot add calendar entries yet, asked for the date,
  and after the edit noted that 4 PM that day had passed.
- The mixed English and Indonesian message was answered in Indonesian only.
- No prompt caching: these prompts stay under the provider's 1,024-token
  minimum. Real conversations with a recent window exceed it.
- Default: **medium**, confirmed by the owner on 2026-10-13.

## Telegram output (tasks 5.1-5.7, 6.3)

| Check | Command | Result |
|---|---|---|
| Gate | `./scripts/check` | 139 passed; pyright 0 errors; OpenSpec strict valid |
| Migration `0003` | `docker compose up --build migrate` | applied to the local database |
| Worker | `REPLY_PROFILE=reply_stub docker compose up -d --build worker` | started; a full 30 s long poll with the new `allowed_updates` raised no error |
| Gateway contract (stream) | `tests/test_gateway_contract.py` on the stub | deltas then the final result; a resubmission replays one final event; a changed body gives `idempotency_conflict` |
| Scripts, free | `./scripts/converse` (25 scripts; the stub answers with each script's `fake_replies`) | 25 ok, USD 0 |
| Scripts, paid | `./scripts/converse --profile reply --paid` (medium) | 25 ok, USD 0.006951 |

Spec coverage (`tests/test_telegram_output_flow.py`, `test_rich.py`,
`test_drafts.py`, `test_telegram_api.py`, `test_updates.py`):
draft then rich message; plain fallback once on rejection; effect sent, and
retried without it when rejected; reaction-only reply; only shared media
kept; quick-reply press answered, redrawn with "✓ label", second press
refused with a notice; poll after the message, vote answered, retracted vote
ignored; Stop discards the reply and the next reply sees the record; a Stop
for another draft is ignored; a Stop stored before a restart closes the turn
without a model call; a regeneration opens a new draft.

Defects found and fixed while testing:

- Parsing: removing unknown `pa-` tags also removed the valid directives;
  `<tg-button` matched `<tg-button-row>` and swallowed the first button; the
  draft view showed a half-written poll. Found by `test_rich.py`.
- Paid run: after a quick-reply press the model answered with only a
  reaction, which needs a typed message, so nothing visible was sent. The
  context note now asks for words after a press or vote, and a reaction with
  no target is sent as the emoji itself (regression test
  `test_a_reaction_to_a_button_press_arrives_as_the_emoji`). Rerun: "Sushi it
  is 🍣" (USD 0.000309).

Formatting choices in the paid run (medium):

- Casual chat stayed plain text; the comparison came as a table; the long
  answer used a block formula, a heading, and a year-by-year table.
- "thanks, got it" got only 👍; the poll request got a native poll and the
  vote an answer; the button request got three quick replies.
- No image URL was invented; the shared one was shown again.
- The celebration got an emoji, not a message effect (the model's choice).
- The mixed-language message was still answered in Indonesian only.
- Prompt caching now works: the guide makes the stable prefix longer than the
  provider's minimum (for example 1,474 of 1,670 input tokens cached).

## Live Telegram check (task 6.1, in progress)

Owner's desktop client, real model, 2026-10-13.

- Defect: each reply left a "…" draft bubble beside it until the owner's next
  message. The placeholder was sent with plain `sendMessageDraft` while the
  text drafts and the reply used the rich methods, contrary to design §15
  (`<tg-thinking>`); Telegram documents that a rich draft is finalised by
  `sendRichMessage`. Fixed in `6b058a5`: every draft, the placeholder
  included, uses `sendRichMessageDraft`
  (`test_placeholder_then_changing_text_then_refresh`,
  `test_empty_draft_is_the_thinking_placeholder_with_stop`). The owner
  confirmed the bubble now disappears when the reply arrives.
- First turns: a burst of three messages merged into one turn and one
  answer; the local time (21.14 WIB) was correct; the formula reply arrived
  rich with no plain fallback; three replies cost USD 0.000583 in total.

## Not verified yet

- Task 6.1 on Telegram with the real model needs the owner to send the
  messages. It also verifies what fakes cannot: rich-message rendering on
  the owner's phone and desktop, the six effect IDs, drafts and Stop, and
  button and poll round trips. The plain-text part: burst, mid-turn follow-up, edits, voice note and photo, a
  forward, a restart while a reply is pending, an offline period, and
  English, Indonesian, and mixed messages. Its results, per-turn cost, and a
  log scan will be added here.
- Long-running behaviour (days of use, the window growing to its budget) and
  laptop sleep during a call.
