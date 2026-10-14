# Verification: add-introduction-profile

Status: tasks 1-5 are verified locally; the live Telegram check (6.1) is
pending.

Branch `feat/introduction-profile` on top of `main` at `b92bb1a`,
2026-10-14. Local only: macOS host, Docker Compose, Postgres 16, the local
llm-gateway (client pinned at `505f6e1`), gpt-6-luna through the `reply`
profile (reasoning effort from the profile, medium).

## Data

Synthetic only: test fixtures and the conversation scripts in
`tests/conversations/`. Paid transcripts are in the git-ignored
`research/eval/`.

## Results

| Check | Evidence | Result |
|---|---|---|
| Migration `0004` applies, re-applies as a no-op, and its constraints hold | `tests/test_migrate.py`; `tests/test_schema.py`: one active and one pending value per field, bounded values, unknown field and status refused, an action button needs its target, only `bot_name` rows have no turn, an active or completed introduction needs its times | Pass |
| Store: drafts, Save, confirm with history, newer proposal replaces older, cancel, remove deletes every version and keeps only the field name, restart from a completed introduction, offer once, bot name queued | `tests/test_profile_store.py` (9 tests) | Pass |
| `<pa-profile>` and `<pa-intro-summary/>` parsing: normalised values, unknown field, bad timezone, over-long name, duplicate field, skip, hidden in drafts | `tests/test_rich.py` | Pass |
| End to end on the fake server: offer once and Later, Save only after the summary and the bot renamed, Edit without saving, `/start` resumes, stated change waits for Confirm with no value in logs, stale Confirm, Cancel, `/profile` with Remove and no model call, removal note, timezone applied to new messages, refused rename keeps the name, command menu replaced once | `tests/test_profile_flow.py` (11 tests) | Pass |
| The confirmed profile follows the guide in the instructions and is identical between turns; drafts and pending values stay in the context note | `tests/test_context.py` | Pass |
| S1 behaviour unchanged | the whole suite: 191 tests | Pass |
| Nine new scripts, free on the fake gateway | `tests/test_conversation_scripts.py`: 39 scripts | Pass |
| `./scripts/check` at `578c057` | lock, format, lint, pyright 0, 191 tests, compose, OpenSpec strict valid | Pass |

## Paid evaluation (task 5.2)

gpt-6-luna, `reply` profile, owner-authorised with the proposal. Total
USD 0.036011.

- **First run, all nine scripts (USD 0.014133):** 8 of 9 passed.
  `intro-edit` failed: after finishing early and pressing Edit, the model
  went back to the next open topic instead of the edit, and no summary
  followed the change. Cause: finishing early left the unasked topics open.
  Fixed in `578c057` (the early finish skips them; the reply to Edit only
  asks what to change; the free script now reproduces the real model's reply
  without `<pa-intro-summary/>` and failed before the fix). Reruns: 4/4 pass.
- **Wording, from reading the transcripts:** the model repeated every
  introduction answer back ("I'd save your style as..."), asked "What
  timezone are you in?" instead of confirming the known one, and said "I'll
  call you Sam" before Confirm. Fixed in `578c057`: the note lists the open
  topics in order and asks for short acknowledgements and a timezone
  confirmation; the persona asks "Want me to remember that?". Reruns of the
  introduction scripts: answers acknowledged in a few words, "Are you in
  Asia/Jakarta, or somewhere else?" and "Zona waktumu Asia/Jakarta?".
- **Final runs on `578c057`:** `intro-english`, `intro-indonesian`,
  `intro-stop-resume`, `intro-edit` (USD 0.004234); `incidental-mentions`,
  `stated-change`, `timezone-change`, `intro-later`, `profile-30-turns`
  (USD 0.008549): 9/9 pass.
- **Measures:** zero false proposals across the incidental mentions (a
  friend's nickname, a reply for a parent, a sibling's job and hours, a
  routine for someone else, a formal reply for a manager); zero questions
  about stored fields in the 30-turn run (it used the stored name, answered
  Indonesian messages in Indonesian, kept replies short except where detail
  was asked for, and listed the profile when asked "remind me what you know
  about me").

## Not verified yet

- Task 6.1, the live Telegram check with the owner on phone and desktop:
  the offer, a full introduction with a skip and an Edit, Save and the new
  bot name, `/profile` with Remove, a confirmed and a cancelled change, and
  the command menu.
