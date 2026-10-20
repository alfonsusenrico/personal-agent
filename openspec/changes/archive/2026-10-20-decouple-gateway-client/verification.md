# Verification

Branch `feat/gateway-api-client`, based on `2733808`, 2026-10-20.

| Check | Result |
|---|---|
| `./scripts/check` (`REQUIRE_DB=1`) | passed: lock, format, lint, Pyright, 231 tests (2 skipped: the opt-in contract tests), Compose config, OpenSpec |
| Every conversation and flow test, including the exactly-once restart scenarios | pass unchanged in intent on the agent's own types |
| Client tests (`tests/test_gateway_api.py`, 21) | `whoami` with the key; an invalid key is an authentication error; an unreachable gateway is a retryable `gateway_unreachable`; a stream yields started, text, and the result, and sends only the given options; a replay is the final result alone; an incomplete answer keeps its reason; `provider_rate_limited`, `concurrency_exhausted`, and `call_in_progress` are resubmitted with the same call ID after about 1 s; a cut stream, a lost connection, and a 5xx without a problem are resubmitted with the same call ID and end with the replayed result; resubmission stops after 4 attempts; a wait past the deadline goes to the caller; `execution_unknown` (before and during the stream), `budget_exhausted` (with its wait), `call_result_expired`, and `profile_not_found` are never resubmitted; an unknown code and a 4xx without a problem are final |
| Fixtures | 9 responses recorded from the published gateway 0.13.0 image with `tests/fixtures/gateway/record.py` (throwaway database and project, stub model); 4 written from the 0.13.0 source and marked so (budget, concurrency, call in progress, result expired); no key in any file |
| Contract test (`PA_CONTRACT_IMAGE` = gateway 0.13.0, `sha256:2e24e95b…`) | 2 passed: the agent's real reply request is accepted, streamed, replayed as one final event, and refused with `idempotency_conflict` when changed; every recorded result field is still returned. With a made-up field added to the expectations it fails and names the field (checked once, then restored) |
| `uv.lock`, `uv tree` | no `llm-gateway-client` and no git source |
| Clean image builds (`--no-cache`, both targets) | build with no gateway access and without git in the build stage; the runtime image has no gateway package and imports the worker; the backup image has `restic` and `pg_dump` |
| Laptop development gateway: `project upgrade-contract personal_agent` (owner-approved in the plan) | the contract was version 1, refused with `contract_upgrade_required`; now version 2 (history v6) with `reply` on `openai-gpt-6-luna` (medium, 2,000 tokens) and `reply_stub` on `stub-echo`; the version-1 export is kept in the ignored `research/backups/` |
| `./scripts/converse` (free `reply_stub` profile, laptop gateway) | 37 of 37 scripts ok through the agent's client; total cost USD 0 |

## Deliberate differences from the old client

- The problem-code table follows gateway 0.13.0. One code changes behaviour:
  `request_capacity` (the gateway refused before reading the request) is now resubmitted
  with the same call ID, as the gateway's reference says; the old client did not know the
  code and failed the turn. The other codes added end the call as failed either way.
- `whoami` turns a lost connection into a retryable `gateway_unreachable`; the old client
  let the transport exception escape, which the worker's startup check did not catch,
  although its comment says an unreachable gateway only logs a warning.
- The concurrency limiter is gone: the agent runs one generation at a time.
- The Dockerfile no longer installs git.

## Not verified here

- A paid reply: not needed (no behaviour change) and not authorised for this change.
- The worker against the real Telegram bot: the laptop bot stays stopped; the flow tests
  and conversation scripts drive the real runner and sender.
