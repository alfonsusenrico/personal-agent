# Proposal

## Why

The assistant's first job is to be someone the owner can simply talk to. Every
later step (recall, facts, reflection, initiative, tools) builds on the
conversation records this step creates, so the data recorded from the first
message onward decides how good memory can become. This change makes the
assistant usable on Telegram and fixes those foundations.

## Expected Outcome

- The owner messages the assistant's own Telegram bot and gets short, natural
  replies:
  - a typing indicator while it thinks;
  - quick bursts of messages answered as one;
  - long answers split cleanly;
  - replies in the language of the owner's latest message;
  - awareness of the current date and time.
- **Restarts:** after a restart or a period offline, every waiting message is
  answered exactly once, and the reply mentions the delay when it was offline.
- **Edits:** an edited message is answered again.
- **Non-text messages** (photos, voice notes, stickers, files) get a short
  "I can't read this yet" reply.
- **Access:** only the owner's Telegram account is answered; anyone else is
  ignored.
- **Records:** every message is kept permanently with its time, timezone,
  source, and trust level. The owner can see each turn's model cost; no
  message content appears in logs.

## What Changes

- **Telegram channel:** a long-polling worker for private chats, its own bot
  token, a deterministic allowlist, and Telegram rendering (typing, splitting,
  replies to older messages).
- **Inbound event log:** an immutable, channel-neutral envelope for every
  update, deduplicated by update ID, with trust and forward origin recorded.
- **Turns:** a burst of messages becomes one turn, answered by one reply.
  - Turns run one at a time per person.
  - A message that arrives mid-turn joins the turn if nothing was sent yet.
  - An outbox makes replies exactly-once across restarts.
- **Replies through the gateway:** each turn is one `llm-gateway-client` call
  with stable call IDs and a per-person prompt cache key.
  - The prompt holds the persona, a recent window, and a per-turn context
    message (date, time, timezone, offline delay, edit notice).
  - `execution_unknown` is handled by apologising and retrying once under a
    new call ID.
- **Records:** content-free per-call usage and cost.
- **Data model:** person, identity, conversation, inbound event, turn,
  message, outbox, model call. Every row carries `person_id`.
- **Evaluation skeleton:** synthetic conversation scripts run against the
  gateway's free stub model.
- **`AGENTS.md`** (changed with this proposal): the current date and time go
  in a per-turn context message, not the instructions, so the stable prompt
  prefix stays cacheable.

## Non-goals

- The introduction session and the learned profile (step 2).
- Summaries, retrieval, embeddings, facts, reflection, and the `/memory`
  commands (steps 3-5).
- Feedback capture (`/note`, reactions): the owner chose to add it later.
- Proactive messages and reminders (step 6).
- Tools, web search, and app access (step 7 onward).
- Reading images, voice, or files; group chats; streaming previews; a web UI;
  deployment.

## Capabilities

### New Capabilities

- `telegram-channel`: receiving Telegram updates, access control, and
  rendering replies on Telegram.
- `conversation`: turns, reply generation through the gateway, context
  layout, delays, edits, and exactly-once replies.
- `message-log`: the permanent, immutable record of inbound events and
  conversation messages, with time, timezone, trust, and provenance.

### Modified Capabilities

None. These are the project's first specs.

### Project capabilities and standards

No new project capability. `database`, `personal-data`, `background-jobs`,
and `llm` are already declared, and this change applies their standards:

- hand-written migrations;
- content-free logs;
- an allowlist on a personal-data channel;
- exactly-once processing in the worker;
- idempotent model calls through stable call IDs.

## Cost

- **Per turn:** about USD 0.0007-0.0012 at medium reasoning effort, assuming
  about 6k input tokens (half cached) and 600-1,500 output tokens at the
  gateway's gpt-6-luna list prices.
- **At 50 turns a day:** about USD 1-1.8 a month.
- The agent's gateway contract requests the full current cap of USD 5 a
  month (owner's decision). Tests use the free stub model.

## Impact

- **New code:** `src/personal_agent/telegram/`, `conversation/`, `gateway/`,
  and `worker.py`.
- **Data:** migrations `0001`-`0002`. New settings for the bot token,
  allowlist, gateway URL and key, and the fallback timezone.
- **Compose:** a `worker` service.
- **New dependencies:** `llm-gateway-client`, pinned by commit from the public
  gateway repository; `httpx2`.
- **Owner actions:**
  - create the bot with BotFather and put its token in the local `.env`;
  - register the agent as a gateway project (keys go straight into `.env`);
  - authorise the paid live checks.
