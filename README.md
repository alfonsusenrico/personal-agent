# Personal Agent

A personal assistant you talk to over Telegram. It aims to feel like a capable
aide rather than a chatbot: it remembers reliably, stays current on your email,
calendar, and projects, and learns the APIs of your own apps well enough to
explain them and use them safely.

It is the first consumer of [LLM Gateway](https://github.com/alfonsusenrico/llm-gateway):
every model call goes through the gateway, which holds the provider keys and
enforces the budget. Everything else (conversation history, memory, web
search, tools, and personal data sync) lives here, with its own keys.

## Status

Early development; everything runs locally for now.

| Area | Status |
|---|---|
| Bootstrap: settings, logging, migrations, checks | Done |
| Telegram conversation through the gateway | Done (local) |
| Profile: names, language, style, learned in conversation | Done (local) |
| Reliable long-term memory | Planned |
| Gmail and Google Calendar sync (read-only) | Planned |
| Learning your apps' APIs (read-only) | Planned |
| Confirmed actions, proactive notices, voice notes | Planned |

## Principles

- **Memory you can trust:** the raw conversation log is kept, facts are
  stored with when they were true and where they came from, and the
  assistant says "I don't know" instead of guessing.
- **Small context per turn:** only what the current message needs, so
  answers stay accurate after long conversations and cost stays low.
- **Read-only by default:** anything that changes data outside the assistant
  needs your explicit confirmation, enforced in code rather than by the
  prompt.
- **Private logs:** logs hold identifiers, counts, and timings only, never
  messages, memories, email, or calendar content.

## Local setup

Requirements: Python 3.13 with [uv](https://docs.astral.sh/uv/), Docker with
Compose, and the [OpenSpec](https://github.com/Fission-AI/OpenSpec) CLI.

```bash
./scripts/setup-local          # create .env once, with a generated database password
uv sync --locked
docker compose up -d db        # local database on 127.0.0.1:54331
./scripts/check                # every check: lock, format, lint, types, tests, compose, OpenSpec
docker compose up --build migrate
```

### Talking to it on Telegram

1. **Bot:** create the assistant's own bot with BotFather and put its token in
   `.env` as `TELEGRAM_BOT_TOKEN`. Don't reuse a bot that another program
   polls: Telegram allows one `getUpdates` consumer per bot.
2. **Allowlist:** put your Telegram user ID in `TELEGRAM_ALLOWED_USER_IDS`.
   Messages from anyone else, and from groups, are ignored and never stored.
3. **Gateway:** from the gateway's directory, register the agent and put
   its development key in `GATEWAY_API_KEY` without printing it. Give it a
   `reply` profile on a real model and a free `reply_stub` profile on the
   stub model:

   ```bash
   docker compose run --rm api llm-gateway-admin project register personal_agent --display-name "Personal Agent"
   docker compose run --rm api llm-gateway-admin project set-request personal_agent --env development --rpm 30 --concurrency 2 --budget-usd 5.00 --cost-ceiling-usd 0.05
   docker compose run --rm api llm-gateway-admin project set-profile personal_agent reply --provider openai --model gpt-6-luna --reasoning-effort medium --max-output-tokens 2000
   docker compose run --rm api llm-gateway-admin project set-profile personal_agent reply_stub --provider stub --model stub-echo
   ```

4. **Run:** `docker compose up -d --build worker`. The worker reaches the
   gateway on the host at `host.docker.internal:8360`. Set
   `REPLY_PROFILE=reply_stub` to try the whole path for free; the stub echoes
   your message.
5. **Logs:** `docker compose logs -f worker` shows identifiers, counts, and
   timings only.

How it behaves:

- While it thinks you see "typing…", then a live draft of the reply as it is
  written, with a Stop button (Telegram Desktop sends Stop as /stop, which
  works the same). Stop discards the reply (the model call is still paid
  for).
- Replies are Telegram rich messages: plain text for chat, and tables,
  lists, code, formulas, or collapsible sections when they help. It can add
  quick-reply buttons (your press counts as your answer), link and copy
  buttons, react to your message with an emoji, add a message effect, send a
  poll (your vote comes back to it), and show images whose URLs you shared.
- Messages sent within 2 seconds of each other are answered as one reply. A
  message sent while it is still thinking makes it reconsider, at most once.
- It replies in the language of your latest message and knows the current
  date and time (`DEFAULT_TIMEZONE`, Asia/Jakarta unless set, or the
  timezone you confirmed).
- **Getting to know you:** it learns what to call you, its own name, your
  language, reply style, timezone, work or study, routine, and interests
  inside normal conversation. It always answers what you said first and
  follows you when you change the subject. Now and then, at a natural moment,
  it asks about one thing it doesn't know yet (at most one such question
  every six of your messages, two tries per topic, never again once you wave
  it off). `/start` asks the open questions now, until you move on.
- **Saved at once:** whatever you tell it about yourself ("call me Sam", "keep
  it short", "I'm in Tokyo this week") is saved right away and shown under the
  reply as "Saved: … → …" with an Undo button. Naming it renames the bot on
  Telegram. `/profile` lists what it knows with a Remove button per entry. A
  change keeps the old value as history; Remove deletes every version.
- After being offline it answers what it missed and mentions the delay;
  edited messages are answered again; photos, voice notes, and files get an
  honest "can't open this yet".
- Nothing is lost across restarts. The one exception is a stop in the
  instant after Telegram accepts a message and before the agent records it,
  which can send that one message twice.
- Each model call's tokens and cost are in the `model_call` table; no
  message text is ever in a log.

### Conversation scripts

`tests/conversations/*.yaml` are synthetic conversations with expected
outcomes. The test suite runs them against a fake gateway. `scripts/converse`
runs them through your gateway and writes a transcript with each call's
tokens and cost to the git-ignored `research/eval/`:

```bash
./scripts/converse                                   # free: the reply_stub profile
./scripts/converse --profile reply --paid            # real model: spends budget
./scripts/converse --profile reply --paid --effort low --only indonesian burst-merge
```

It needs `TEST_DATABASE_URL`, `GATEWAY_TEST_URL`, and `GATEWAY_TEST_API_KEY`
in `.env`; the same gateway settings enable the gateway contract test.

## Development

- Changes are planned as OpenSpec changes in `openspec/changes/`; accepted
  requirements live in `openspec/specs/`.
- `./scripts/check` must pass before every commit.
- Commit subjects follow Conventional Commits.

## License

[MIT](LICENSE)
