# Proposal

## Why

The assistant has no name, does not know how to address the owner, and only
follows the language of the latest message. The vision's "knows my style"
test needs it to use the owner's chosen names, language, and tone, and to
stop asking things it already knows. Every later step (facts, reflection,
initiative) also reads this profile, so it comes next.

## Expected Outcome

- **Getting to know the owner, gradually:** the assistant learns what to call
  the owner, its own name, language, reply style, timezone, work or study,
  daily routine, and interests inside normal conversation. Every reply first
  answers what the owner said and follows topic jumps. At a natural moment it
  may ask about one thing it does not know yet: at most one such question
  every six owner turns, at most two attempts per topic, and never again for
  a topic the owner waves off. The timezone is confirmed, not asked.
- **`/start`:** asks the open questions now, one per reply, while the owner
  keeps answering; any other topic ends it.
- **Saved at once:** whatever the owner states about themselves or about how
  the assistant should behave ("call me Sam", "keep replies short", "I'm in
  Tokyo this week", or an answer to its question) is saved immediately and
  shown under the reply as "Saved: What to call you → Sam" with an Undo
  button. Nothing is inferred silently.
- **Afterwards:** replies use the stored names, language, and style, and the
  assistant does not ask about anything already stored. The bot's Telegram
  name follows the assistant's name.
- **`/profile`:** shows every stored entry with when it was set, with a
  Remove button per entry. A change keeps the old value as dated history;
  Remove deletes the entry and all its earlier versions.
- **Menu:** the bot's command menu lists this project's commands only
  (`/start`, `/profile`, `/stop`), replacing about 65 stale commands left
  from an earlier setup on the same bot.

## Owner decisions

- 2026-10-14 (discussion): topics names, language, reply style, work or
  study, routine, interests (not people close to the owner); the name renames
  the bot (bot-wide); explicit statements only; changes keep history, Remove
  deletes.
- 2026-10-14 (live check): the first version ran the introduction as a mode
  (one topic per reply in a fixed order, values held until a Save at the
  end). The owner rejected it as scripted: it kept returning to the open
  question and a stated change sat unsaved. Replaced by the gradual
  behaviour above; values are saved at once with Undo; there is no offer,
  and `/start` stays as an optional shortcut.

## What Changes

- **Profile store:** versioned entries per person and field (active or
  superseded), with their source messages, the entry each one replaced, and
  when it was saved. Fields: `address`, `assistant_name`, `language`,
  `reply_style`, `timezone`, `work`, `routine`, `interests`.
- **Topic pacing:** per person and topic, how often it was asked, when, and
  whether the owner declined it; whether `/start` asked for questions now.
- **Capturing values without extra calls:** the reply model marks a stated
  value with `<pa-profile field="...">value</pa-profile>`, a declined topic
  with `skip="true"`, and a getting-to-know question with
  `<pa-ask field="..."/>`. Code validates values (known field, length, a real
  IANA timezone, at most 64 characters for the name) and saves them.
- **Action buttons:** Undo and Remove, handled by code and executed once.
- **Prompt:** the active profile is added to the stable instructions after
  the persona and Telegram guide, so it stays cached; the per-turn context
  note lists what is still unknown only when pacing allows a question.
- **Telegram:** `setMyName` when the assistant's name is saved or restored,
  sent through the outbox; `setMyCommands` at worker start.
- **Persona:** uses the stored name; without one it says the owner can give
  it one.

## Non-goals

- Inferring preferences from behaviour (S5 reflection) and facts about other
  topics or people (S4); people close to the owner are not asked (owner
  decision).
- Profiles for anyone other than the owner (registered relatives come
  later; the profile is per person already).
- Quiet hours and initiative settings (S6).
- Sharing a location to set the timezone (a later change, owner decision).
- The bot's profile photo, description, and short description.
- Deleting the raw messages a removed entry came from: the message log
  stays permanent.

## Capabilities

### New Capabilities

- `owner-profile`: learning the profile gradually in conversation, saving
  with Undo, `/start`, `/profile`, and how the profile shapes replies.

### Modified Capabilities

- `telegram-channel`: action buttons handled by code, the bot's name and
  command menu set through the Bot API.

### Project capabilities and standards

No new project capability. The profile is personal data under the declared
`personal-data` capability: never logged, shown only to its owner, removable
on request. Its migration is hand-written and additive (`database`).

## Cost

- **No extra model calls:** values and questions are marked in the reply
  itself.
- **Profile in the prompt:** about 150-300 tokens in the cached prefix;
  each profile change costs one uncached prefix on the next call (about
  USD 0.0005).
- **Evaluation:** conversation scripts on the real model, about USD 0.05 in
  total (owner-authorised with approval).

## Impact

- **New code:** `src/personal_agent/profile/` (store, directive handling,
  pacing, `/profile` rendering), changes in `conversation/` (prompt, context
  note, commit) and `telegram/` (action buttons, bot name, commands).
- **Data:** migration `0004` (profile entries and removals, topic pacing,
  button actions, the `/profile` command, an outbox kind for the bot's
  name). Forward-only and additive.
- **Fixed text:** "Saved:" lines and `/profile` use English labels; values
  are the owner's own words.
