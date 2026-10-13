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
Language: code, identifiers, logs, and docs are English. The assistant's
reply language is set in the conversation change.

## Commands

```bash
./scripts/setup-local                  # create .env once (generated local DB password)
uv sync --locked
docker compose up -d db                # database for tests marked db (127.0.0.1:54331)
./scripts/check                        # every check: lock, format, lint, pyright, tests, compose, OpenSpec
uv run ruff format                     # apply formatting
docker compose up --build migrate      # apply migrations to the local database
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
- `tests/`: tests marked `db` need `TEST_DATABASE_URL`; `REQUIRE_DB=1` makes a
  missing database fail.
- The phased plan is local-only in `research/notes/`; this repository is
  public, so `research/` is ignored by git. Specs are in `openspec/specs/`.

## Project rules

- Phase work follows the approved plan. Each phase's design starts by
  reviewing current official documentation (Telegram Bot API, Google APIs,
  the gateway's own specs).
- Model calls use stable `provider_call_id`s, so a resubmission replays
  instead of paying twice. An `execution_unknown` call is never resubmitted
  under the same ID.
- Development and tests use the gateway's free stub model. Live model calls
  cost real money: only run them for tasks the owner authorised.
- Every record carries `person_id`, so other people can be added later
  without a data migration.
- Put the current date, time, and timezone in a per-turn context message on
  every turn; the model does not know them. Never put them in the
  instructions: a changing prefix defeats prompt caching.
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
the gateway's README and specs for the call contract.
