# Tasks

## 1. Data model

- [ ] 1.1 Migration `0004`: `profile_entry` (active or superseded, one active
  per field, `replaces_id`), `profile_removal`, `profile_topic`,
  `profile_pacing`, `reply_button.action` and `target_id`, the `command`
  event kind and `turn.command`, and the `bot_name` outbox kind. Verify with
  migration tests (applies on the S1 schema and again as a no-op) and schema
  tests for each constraint.
- [ ] 1.2 Move the owner's local database from the first version of `0004`
  to this one, keeping saved values (one-off, not committed). Verify: the
  migration runner accepts the database and `/profile` lists the same values.

Expected Result: the schema holds versioned values, removals, pacing, and
button actions; nothing uses it yet.

## 2. Profile store and directives

- [ ] 2.1 Store: save (supersede the old value, record what it replaced),
  undo a reply's values (restore what they replaced), remove (delete every
  version, record the field only), the timezone link to `person.timezone`,
  topic asks and declines, and `/start` asking now. Verify with database
  tests for each operation.
- [ ] 2.2 Parse and validate `<pa-profile>` (value or skip) and `<pa-ask>`:
  known field, length limits, IANA timezone, one per field. Verify with
  `test_rich.py` cases.
- [ ] 2.3 Commit path: marked values saved at once with "Saved:" lines and an
  Undo button, declines and questions recorded, the bot's name queued when it
  changes. Verify with flow tests on the fake server and gateway.

Expected Result: what the owner states is saved with its sources and shown
with Undo; questions and declines are counted.

## 3. Buttons, commands, and pacing

- [ ] 3.1 Undo and Remove handled in the poller transaction (act once,
  redraw, stale notice), Undo restoring the bot's name. Verify with tests for
  each action, a repeated press, and a stale press.
- [ ] 3.2 `/profile` rendered by code with Remove buttons; `/start` asking
  now until a reply asks nothing. Verify with tests: no model call for
  `/profile`, the asking-now note, and its end.
- [ ] 3.3 Pacing: open topics in the context note only when `/start` asked or
  six owner turns passed since the last question; at most two asks per
  topic; declined topics never. Verify with context and flow tests.
- [ ] 3.4 `bot_name` outbox rows sent with `setMyName`; `setMyCommands` with
  `start`, `profile`, `stop` at worker start only when the menu differs.
  Verify with fake-server tests.

Expected Result: the profile grows inside normal conversation at a bounded
pace, and every code-handled button acts once.

## 4. Prompt

- [ ] 4.1 Profile section in the instructions, persona rules (name, address,
  language and style, no re-asking, mark only direct statements, answer
  first and follow topic jumps), and the timezone confirmation. Verify with
  context tests: the section follows the guide and the prefix is unchanged
  between two turns with no profile change.

Expected Result: replies are shaped by the saved profile and never steered by
what is unknown.

## 5. Evaluation and documentation

- [ ] 5.1 Scripts from design decision 9 in `tests/conversations/`, run on the
  stub (free). Verify: all pass in `./scripts/check`.
- [ ] 5.2 Owner-authorised: run them on the `reply` profile (about
  USD 0.05). Verify: structural checks pass; transcripts show no question
  about a stored field, no ignored topic jump, nothing saved from incidental
  mentions; record results and cost in `verification.md`.
- [ ] 5.3 Update `AGENTS.md` (architecture map: `profile/`) and `README.md`.
  Verify: `./scripts/check` passes.

Expected Result: the evaluation shows the profile is learned naturally,
saved visibly, and used without re-asking.

## 6. Live verification and delivery

- [ ] 6.1 Owner-authorised live check on Telegram (phone and desktop): a
  first conversation where it learns something at a natural point, a topic
  jump after a question, a stated change with Undo, `/start`, `/profile` with
  Remove, the bot's name, the menu. Verify: results, per-turn cost, and a log
  scan with no profile value in `verification.md`.
- [ ] 6.2 Run `./scripts/check`, open a PR for `feat/introduction-profile`,
  and report its URL and the verification record; the owner merges.

## Workflow follow-up

- After the owner merges: verify the merge, then archive the change in a
  docs PR.
