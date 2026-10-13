# Verification: add-telegram-conversation

Status: the plain-text version below is verified. On 2026-10-13 the owner
folded Telegram's full output features into this change (tasks 5.1-5.7);
their verification is added when they are implemented.

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
  ("I can't meet in person"). This is a persona question for the owner.
- The mixed English and Indonesian message was answered in Indonesian only.
- No prompt caching: these prompts stay under the provider's 1,024-token
  minimum. Real conversations with a recent window exceed it.
- Default kept: **medium**, the owner's stated preference, pending the
  owner's review of the transcripts (`research/eval/`, local only).

## Not verified yet

- Task 6.1 on Telegram with the real model needs the owner to send the
  messages: burst, mid-turn follow-up, edits, voice note and photo, a
  forward, a restart while a reply is pending, an offline period, and
  English, Indonesian, and mixed messages. Its results, per-turn cost, and a
  log scan will be added here.
- Long-running behaviour (days of use, the window growing to its budget) and
  laptop sleep during a call.
