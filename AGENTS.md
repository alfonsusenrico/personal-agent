# Personal Agent

## Purpose and boundaries

The owner's personal assistant over Telegram, and the first consumer of
`llm-gateway`.

- **The agent owns:** the Telegram conversation, the raw message log, memory
  (facts, episodes, retrieval, any embeddings), tools and the tool loop, web
  search, Google sync, app learning, and every third-party key it uses.
- **The gateway owns:** provider keys, model execution, budget grants, and the
  cost ledger. Every model call goes through it with this project's key; the
  agent never holds a model provider key for chat models.
- **It never:**
  - logs messages, memories, prompts, model output, email, or calendar
    content;
  - writes to an external system without the owner's confirmation, enforced
    in code;
  - stores or caches web search results (search provider terms).

Capabilities: database, personal-data, background-jobs, llm
Design surface: Telegram conversation (no web UI)
Language: code, identifiers, logs, and docs are English. The assistant
replies in the language of the owner's latest message; fixed notices are in
English and Indonesian.

## Commands

```bash
./scripts/setup-local                  # create .env once (generated local DB password)
uv sync --locked
docker compose up -d db                # database for tests marked db (127.0.0.1:54331)
./scripts/check                        # every check: lock, format, lint, pyright, tests, compose, OpenSpec
uv run ruff format                     # apply formatting
docker compose up --build migrate      # apply migrations to the local database
./scripts/test -q tests/test_context.py   # pytest with the local test settings from .env
./scripts/converse                     # conversation scripts on the gateway stub (free)
./scripts/review --summary             # open flag counts and backup age (agents: --summary only)
./scripts/restore-check                # restore the newest backup into a throwaway database
REPLY_PROFILE=reply_stub docker compose up -d --build worker   # the bot, free stub replies
```

## Architecture map

- `src/personal_agent/settings.py`: the one typed settings object. Errors
  name variables, never values.
- `src/personal_agent/logging_setup.py`: content-free logging; JSON in
  production.
- `src/personal_agent/db/`:
  - `pool.py`: the async pool.
  - `migrate.py`: the checksummed, forward-only runner.
  - `migrations/NNNN_name.sql`: hand-written migrations.
- `src/personal_agent/worker.py`: the worker process (advisory lock, startup
  checks, poller, turn runner, sender).
- `src/personal_agent/telegram/`: Bot API client (`api.py`), update
  envelope and allowlist (`updates.py`), store-then-confirm poller (button
  presses, votes, Stop), outbox sender with plain-text fallback, streamed
  drafts (`drafts.py`), reply rendering (`rich.py`: directives, buttons,
  media rule, draft view), splitting (`render.py`), and the command menu
  (`commands.py`).
- `src/personal_agent/conversation/`:
  - `store.py`: all conversation SQL (ingest, turns, answers, outbox).
  - `runner.py`: one generation at a time, debounce, regeneration, commit.
  - `context.py`, `persona.md`, and `channels/telegram.md`: the reply call
    layout (persona and platform guide, window, per-turn context note, the
    owner's turn).
  - `notices.py`: fixed bilingual apology and budget notice.
- `src/personal_agent/profile/`: the owner profile and introduction.
  - `fields.py`: fields, labels, and value rules.
  - `store.py`: versioned entries (active, superseded) with Undo, removal,
    and the pacing of getting-to-know questions.
  - `apply.py`: a reply's `<pa-profile>` and `<pa-ask>` marks: values saved
    at once with a "Saved:" line and Undo; questions and declines recorded.
  - `actions.py`: Undo and Remove, run in the press's transaction.
  - `render.py`: "Saved:" lines, `/profile`, the instructions section, and
    the per-turn notes (open topics only when pacing allows).
- `src/personal_agent/feedback/`: reply feedback on `message` rows, never on
  a channel's message (`store.py`), and the local review (`review.py`).
  Channels map their own gesture in their update parser (Telegram: 👎 and
  `/note` in `telegram/updates.py`).
- `src/personal_agent/backup/`: restic commands (`restic.py`), the daily
  backup service (`service.py`), and the restore check (`restore_check.py`).
  They run in the image's `backup` target, which adds `restic` and a
  PostgreSQL 16 client.
- `src/personal_agent/gateway/api.py`: the agent's own gateway client
  (`whoami`, streamed `responses`), the problem-code table, and same-ID
  resubmission. `tests/fixtures/gateway/` holds recorded responses and
  `record.py` to record them again.
- `src/personal_agent/gateway/calls.py`: call IDs
  (`pa:{turn.call_key}:{purpose}:{generation}:a{attempt}`), retries after
  `execution_unknown`, and content-free `model_call` records.
- `tests/`: tests marked `db` need `TEST_DATABASE_URL`; `REQUIRE_DB=1` makes a
  missing database fail. `fakes.py` and `harness.py` drive the real poller,
  runner, and sender against a fake Telegram server and a fake gateway;
  `conversations/*.yaml` are the synthetic conversation scripts.
- The phased plan is local-only in `research/notes/`; this repository is
  public, so `research/` is ignored by git. Specs are in `openspec/specs/`.

## Project rules

- Phase work follows the approved plan. Each phase's design starts by
  reviewing current official documentation (Telegram Bot API, Google APIs,
  the gateway's own specs).
- Model calls use stable `provider_call_id`s, so a resubmission replays
  instead of paying twice. An `execution_unknown` call is never resubmitted
  under the same ID. Call IDs are built from a random key stored on the row
  (`turn.call_key`), never from a database sequence: the gateway remembers
  IDs, and a reset or restored database would reuse sequence values.
- Development and tests use the gateway's free stub model. Live model calls
  cost real money: only run them for tasks the owner authorised.
- The gateway is reached only through its HTTP API with the agent's own
  client; never install or import gateway code or packages. When the gateway's
  API changes, record the fixtures again and update the contract image digest.
- Every record carries `person_id`, so other people can be added later
  without a data migration.
- The model writes each platform's own format from that platform's guide
  (`conversation/channels/`); a new platform gets its complete guide and
  renderer when it is added, never a plain-text interim (owner, 2026-10-13).
  Directives the agent performs itself use the `pa-` prefix.
- Put the current date, time, and timezone in a per-turn context message on
  every turn; the model does not know them. Never put them in the
  instructions: a changing prefix defeats prompt caching.
- Agents run `./scripts/review` only with `--summary`: the full review prints
  the owner's messages and belongs on the owner's own terminal.
- Feedback and other owner gestures are stored channel-neutral; each channel
  maps its own mechanism to them.
- Migrations run as the compose `migrate` step, never at startup. An
  applied migration file never changes.
- The repository is public on GitHub. Tracked files hold no secrets, no
  personal data, no personal infrastructure details, and nothing about the
  owner's employer or other organisations' systems. Planning notes stay in
  the git-ignored `research/`.
- Code reaches `main` only through owner-reviewed pull requests from topic
  branches. Everything runs locally until the owner accepts it for
  deployment.

## Overrides

- `default:ci`: hosted CI is on hold until the owner accepts the agent for
  deployment (owner, 2026-10-13). `./scripts/check` is the gate and runs
  before every commit. The framework hooks enforce commit messages and
  protected-branch pushes.

## References

`openspec/specs/`; `openspec/changes/`; the local plan in `research/notes/`;
the gateway's published API reference for the call contract.
