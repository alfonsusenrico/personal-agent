# Tasks

## 1. Data model

- [ ] 1.1 Add migration `0004`: `profile_entry` (one active and one pending
  row per person and field), `introduction`, `reply_button.action` and
  `target_id`, and the `bot_name` outbox kind. Verify with migration tests:
  applies on the S1 schema and as a no-op again; the partial unique indexes
  and checks reject a second active row, an action without its target, and
  an unknown status.

Expected Result: the schema holds drafts, pending changes, history, and
introduction state; nothing uses it yet.

## 2. Profile store and directive

- [ ] 2.1 Add the profile store: draft, propose, confirm (supersede the old
  value), cancel, remove (delete every version), Save for all drafts, and the
  timezone link to `person.timezone`, each in one transaction. Verify with
  database tests for each operation, including remove leaving no rows and
  the source messages untouched.
- [ ] 2.2 Parse and validate `<pa-profile>` in replies: known field, length
  limits, IANA timezone, one per field; invalid directives dropped and
  counted. Verify with `test_rich.py` cases for every rule.
- [ ] 2.3 Commit path: during the introduction a valid directive stores a
  draft; otherwise a pending change plus Confirm and Cancel buttons on the
  reply. Verify with flow tests on the fake server and gateway.

Expected Result: values from replies are stored as drafts or pending changes
with their source events, and nothing becomes active without the owner.

## 3. Action buttons and commands

- [ ] 3.1 Handle action button presses in the poller transaction (act once,
  redraw with the outcome, stale press notice). Verify with tests for each
  action, a repeated press, and a stale press.
- [ ] 3.2 `/profile` rendered by code with Remove buttons, and `/start`
  beginning or resuming the introduction. Verify with tests: no model call
  for `/profile`, empty profile text, resume keeps drafts.
- [ ] 3.3 `bot_name` outbox rows sent with `setMyName`, and `setMyCommands`
  at worker start only when the menu differs. Verify with fake-server tests:
  rate limit retried, permanent error recorded with the profile kept, no
  second `setMyCommands` on restart.

Expected Result: every code-handled button acts once, and the bot's name and
menu follow the profile.

## 4. Introduction and prompt

- [ ] 4.1 Offer once (offer sentence requested in the context note, Start
  and Later appended, `offered` stored with the answer), and the active
  introduction note listing answered, skipped, and remaining topics. Verify
  with flow tests: offer appears once, Later prevents it, a restart does not
  repeat it.
- [ ] 4.2 Summary rendered by code from drafts with Save and Edit. Verify
  with tests: summary lists exactly the drafts; Save activates all; Edit
  keeps the introduction active.
- [ ] 4.3 Profile section in the instructions, persona and guide rules (name,
  address, language and style, no re-asking, directive only for direct
  statements). Verify with context tests: the section follows the guide,
  drafts and pending values never appear in the instructions, and the prefix
  is unchanged between two turns with no profile change.

Expected Result: the introduction runs end to end on the fake server and the
saved profile shapes the next reply's instructions.

## 5. Evaluation and documentation

- [ ] 5.1 Add the scripts from design decision 9 to `tests/conversations/`
  and run them on the stub (free). Verify: all pass in `./scripts/check`.
- [ ] 5.2 Owner-authorised: run the scripts on the `reply` profile (about
  USD 0.05). Verify: structural checks pass, zero re-asks in the 30-turn
  script, zero false proposals; record transcripts' results and cost in
  `verification.md`.
- [ ] 5.3 Update `AGENTS.md` (architecture map: `profile/`), `README.md`, and
  `.env.example` if settings change. Verify: `./scripts/check` passes.

Expected Result: the evaluation shows the profile is learned, confirmed, and
used without re-asking.

## 6. Live verification and delivery

- [ ] 6.1 Owner-authorised live check on Telegram (phone and desktop): the
  offer, a full introduction with one skip and one Edit, Save, the bot's new
  name, `/profile` with a Remove, a stated change confirmed and one
  cancelled, the menu. Verify: results, per-turn cost, and a log scan with no
  profile value in `verification.md`.
- [ ] 6.2 Run `./scripts/check`, push `feat/introduction-profile`, open a PR,
  and report its URL and the verification record; the owner merges.

## Workflow follow-up

- After the owner merges: verify the merge, then archive the change in a
  docs PR.
