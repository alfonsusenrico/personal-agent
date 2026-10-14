# Verification: add-introduction-profile

Status: verified locally on the revised design (gradual learning, saved
with Undo); the owner accepted the live Telegram check on 2026-10-14.

Branch `feat/introduction-profile` on top of `main` at `b92bb1a`,
2026-10-14. Local only: macOS host, Docker Compose, Postgres 16, the local
llm-gateway (client pinned at `505f6e1`), gpt-6-luna through the `reply`
profile (reasoning effort from the profile, medium).

## Data

Synthetic only: test fixtures and the conversation scripts in
`tests/conversations/`. Paid transcripts are in the git-ignored
`research/eval/`.

## First version and why it was replaced

The first version (`b053593`..`1361e17`) ran an introduction mode: a
one-time offer, one topic per reply in a fixed order, values held as drafts
until a Save at the end, and a Confirm for changes outside it. Live on
Telegram the owner found:

- the model's own "Mulai" button on the offer started nothing (fixed in
  `d59a5aa`, then removed with the offer);
- the timezone was asked instead of confirmed when one answer covered two
  topics (`0b8b5b8`);
- no reply after Save, the summary in one paragraph, a slow rename
  (`eb0aefe`);
- `/start` after Save re-entered question mode and held a name change as an
  unsaved answer (`2d47226`).

The owner then rejected the mode itself as scripted: every reply steered back
to the open question and could not follow a topic jump. Owner decisions,
2026-10-14: learn gradually inside conversation, save at once with Undo, no
offer, `/start` optional. The artifacts were revised (`d43737c`) and the
feature rebuilt (`2f2fd81`, `dd2c48b`, `dabc96e`).

## Results (revised design)

| Check | Evidence | Result |
|---|---|---|
| Migration `0004` applies, re-applies as a no-op, and its constraints hold | `tests/test_migrate.py`; `tests/test_schema.py`: one active value per field with history, bounded values, unknown field and status refused, an action button needs its target, only `bot_name` rows have no turn, one pacing row per topic | Pass |
| Store: save keeps history, Undo restores what a reply replaced (timezone included) and goes stale after a later change, Remove deletes every version and records only the field, asks and declines counted, `/start` asking now, bot name queued | `tests/test_profile_store.py` (7 tests) | Pass |
| Directives: `<pa-profile>` values and skips, `<pa-ask>` per known topic, a timezone written with a note ("Asia/Tokyo (this week)"), hidden in drafts | `tests/test_rich.py`, `tests/test_profile_store.py` | Pass |
| End to end on the fake server: saved at once with Undo and no value in logs, Undo restores the earlier value and the bot's name, stale Undo, paced questions (none for five turns after one) and a declined topic dropped, `/start` until a reply asks nothing, `/profile` with Remove and no model call, removal note, timezone applied to new messages, refused rename keeps the name, command menu replaced once | `tests/test_profile_flow.py` (9 tests) | Pass |
| Notes and prompt: open topics only when a question is allowed, the asking-now note, timezone confirmation, a topic asked twice or declined is not open, the saved profile is identical between turns | `tests/test_context.py` | Pass |
| S1 behaviour unchanged | the whole suite | Pass |
| Scripts, free on the fake gateway | `tests/test_conversation_scripts.py`: 37 scripts, 8 of them for the profile | Pass |
| `./scripts/check` at `dabc96e` | lock, format, lint, pyright 0, tests, compose, OpenSpec strict valid | Pass |

## Paid evaluation (task 5.2)

gpt-6-luna, `reply` profile, owner-authorised with the proposal. Total for
the change: USD 0.085935 (USD 0.040413 on the first version, USD 0.045522 on
the revised one), above the proposal's estimate of about USD 0.05 because
of the redesign.

- **First full run of the eight revised scripts (USD 0.013020):** 7 of 8.
  `timezone-change` failed: the reply said "I'll use Tokyo time this week"
  but nothing changed. The model's raw output (read from the gateway's stored
  result of the synthetic call) was `<pa-profile field="timezone">Asia/Tokyo
  (this week)</pa-profile>`, which validation dropped. Fixed in `dd2c48b`:
  the IANA name inside the value is taken, and the persona says the value is
  only the name. Five reruns: 5/5.
- **Too passive:** the model then asked nothing even at a greeting, and after
  `/start` stopped after one question. Fixed in `dabc96e` (the notes say to
  ask at a natural moment and to keep asking after `/start` while the owner
  answers). Reruns: a greeting gets "Morning! What should I call you?", no
  further question while the owner asks about breakfast and podcasts;
  `/start` asks topic by topic and stops at "convert 5 miles to km".
- **Final runs on `dabc96e`:** all eight pass. Topic jump: one question at
  "slow day today", then the egg questions are answered without returning to
  it. Declined: "not now" gets "No worries." and nothing more. Incidental
  mentions: nothing saved. 30 turns with a full profile: zero questions about
  stored fields.

## Owner's local database (task 1.2)

The first `0004` had been applied to the owner's local database. Before the
change: a backup (`research/backups/2026-10-14-before-0004-rework.dump`,
pg_dump custom format, 14 tables listed). The first version's tables and
columns were removed, the 27 buttons of the old flow deleted (pressing one
now says it is no longer available), the new `0004` applied by the migrate
service, and the saved values restored with their history. The owner's last
explicit request, a name change held unsaved by the first version, was
applied as the current address. The worker started on `dabc96e` and set
the menu to `/start`, `/profile`, `/stop`.

## Live check (task 6.1)

Owner-run on Telegram Desktop, 2026-10-14, worker `dabc96e`, real model
(`reply` profile). Accepted by the owner.

| Check | Evidence | Result |
|---|---|---|
| Normal chat is answered, not steered | five turns (a dinner idea, a cooking follow-up, a request for steps, feedback); every reply answered the message; no getting-to-know question was asked (none recorded in `profile_topic`), which the design allows | Pass |
| A stated preference is saved at once with Undo | the owner's feedback on formatting saved `reply_style` (one active entry), shown as "Saved: Reply style → …" with an Undo button in the same reply | Pass |
| `/profile` answered by code | one `command` turn, no model call | Pass |
| Bot name and menu | the chat header shows the name the owner chose; the menu button is present | Pass |
| Cost | 5 model calls, all completed, none replayed: USD 0.001706 (about USD 0.00034 a turn), 16,931 input tokens of which 8,200 cached, 1,062 output (752 reasoning), average latency 3.95 s | Recorded |
| Logs | 28 worker lines in the window, no warnings or errors; profile lines name the field only; no saved value or message text found | Pass |

Not exercised live in this session, accepted by the owner on the strength of
the tests and the paid scripts above: pressing Undo, pressing Remove,
`/start` followed by a change of subject, and the phone client.
