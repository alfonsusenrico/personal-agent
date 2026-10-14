# Proposal

## Why

The assistant has no name, does not know how to address the owner, and only
follows the language of the latest message. The vision's "knows my style"
test needs it to use the owner's chosen names, language, and tone, and to
stop asking things it already knows. Every later step (facts, reflection,
initiative) also reads this profile, so it comes next.

## Expected Outcome

- **Offer:** on the owner's next message after this change ships, the
  assistant answers normally and then offers a short introduction once, with
  Start and Later buttons. After Later it never offers again; `/start` begins
  or resumes the introduction at any time.
- **Introduction:** about 8 short questions, one at a time, each skippable,
  with buttons where the answer is a choice:
  - what to call the owner, and the assistant's own name (it may suggest a
    few as buttons);
  - language (default language, and whether to mix English and Indonesian);
  - reply style (length, tone, emoji, humour);
  - work or study, daily routine, interests;
  - the timezone, confirmed.
- **Summary:** the introduction ends with one summary of exactly what will be
  stored, with Save and Edit buttons. Nothing is saved before Save. Stopping
  halfway keeps the answers as a draft that `/start` resumes.
- **Afterwards:** replies use the chosen names, language, and style, and the
  assistant does not ask again about anything in the profile. On Save, the
  bot's Telegram name becomes the chosen name.
- **Later changes:** when the owner states a change directly ("call me Sam
  from now", "keep replies shorter", "I'm in Tokyo this week"), the reply
  adds a one-tap Confirm and Cancel; only Confirm saves it. Nothing is
  inferred silently.
- **`/profile`:** shows every stored entry with when it was set, with a
  Remove button per entry. A change keeps the old value as dated history;
  Remove deletes the entry and all its earlier versions.
- **Menu:** the bot's command menu lists this project's commands only
  (`/profile`, `/stop`), replacing about 65 stale commands left from an
  earlier setup on the same bot.

## What Changes

- **Profile store:** versioned entries per person and field, with their
  source messages, status (draft, pending, active, superseded), and
  confirmation time. Fields: `address`, `assistant_name`, `language`,
  `reply_style`, `timezone`, `work`, `routine`, `interests`.
- **Introduction state:** offered, declined, active, completed, per person.
- **Capturing answers without extra calls:** the reply model marks an answer
  or a stated change with a `<pa-profile field="...">value</pa-profile>`
  directive. Code validates it (known field, length, a real IANA timezone,
  at most 64 characters for the name) and stores it as a draft during the
  introduction or as a pending change otherwise.
- **Action buttons:** buttons whose press is handled by code rather than
  answered by the model (Start, Later, Save, Edit, Confirm, Cancel, Remove),
  stored with their action and target and executed once.
- **Prompt:** the active profile is added to the stable instructions after
  the persona and Telegram guide, so it stays cached; while the introduction
  runs, the per-turn context note lists the answered and remaining topics.
- **Telegram:** `setMyName` on Save or a confirmed name change, sent through
  the outbox; `setMyCommands` at worker start.
- **Persona:** uses the stored name; without one it keeps saying a name will
  be chosen soon.

## Non-goals

- Inferring preferences from behaviour (S5 reflection) and facts about other
  topics or people (S4); people close to the owner are not asked (owner
  decision).
- Profiles for anyone other than the owner (registered relatives come
  later; the profile is per person already).
- Quiet hours and initiative settings (S6).
- The bot's profile photo, description, and short description.
- Deleting the raw messages a removed entry came from: the message log
  stays permanent.

## Capabilities

### New Capabilities

- `owner-profile`: the introduction, the versioned profile, explicit changes
  with confirmation, `/profile`, and how the profile shapes replies.

### Modified Capabilities

- `telegram-channel`: action buttons handled by code, the bot's name and
  command menu set through the Bot API.

### Project capabilities and standards

No new project capability. The profile is personal data under the declared
`personal-data` capability: never logged, shown only to its owner, removable
on request. Its migration is hand-written and additive (`database`).

## Cost

- **No extra model calls:** answers and changes are captured from the reply
  itself.
- **Profile in the prompt:** about 150-300 tokens in the cached prefix;
  each profile change costs one uncached prefix on the next call (about
  USD 0.0005).
- **Introduction:** about 10 turns once, about USD 0.01.
- **Evaluation:** scripted introductions and 30-turn re-ask checks on the
  real model, about USD 0.05 in total (owner-authorised with approval).

## Impact

- **New code:** `src/personal_agent/profile/` (store, directive handling,
  summary and `/profile` rendering), changes in `conversation/` (prompt,
  context note, commit) and `telegram/` (action buttons, bot name, commands).
- **Data:** migration `0004` (profile entries, introduction state, button
  actions, an outbox kind for bot settings). Forward-only and additive.
- **Owner actions:** approve this proposal; run the introduction live.
- **Fixed text:** the summary and `/profile` list fields with English
  labels; values are the owner's own words.
