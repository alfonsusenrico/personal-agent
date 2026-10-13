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
| Telegram conversation through the gateway | Planned |
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

## Development

- Changes are planned as OpenSpec changes in `openspec/changes/`; accepted
  requirements live in `openspec/specs/`.
- `./scripts/check` must pass before every commit.
- Commit subjects follow Conventional Commits.

## License

[MIT](LICENSE)
