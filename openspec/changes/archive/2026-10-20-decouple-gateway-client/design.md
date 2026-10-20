# Design

## Context

The agent uses a small part of the client: `GatewayClient(url, key)`, `whoami()` at
startup, `responses.stream(...)` with `provider_call_id`, `operation`, `input`, `profile`,
`instructions`, `max_output_tokens`, `reasoning_effort` and `prompt_cache_key`, and
`aclose()`. `gateway/calls.py` already hides the client behind a `Responses` protocol
and branches on error classes: `RetryableError` (wait and resubmit the same ID),
`ExecutionUnknownError` (new ID, at most one more attempt), `BudgetExhaustedError` (the
budget notice), `GatewayError` codes `idempotency_conflict` and `call_result_expired`
(new ID), and a stream that ends without its final event. Flow tests drive that protocol
with scripted fakes built on the client's event types.

## Decisions

### 1. An owned thin client with the same meanings

`personal_agent/gateway/api.py` holds `GatewayApi(base_url, key)` with `whoami()` and
`stream(...)`, the event types (`Started`, `TextDelta`, `Completed`, `CallResult`,
`Usage`) with only the fields the agent reads, and the error classes. The problem-code
table is copied from the gateway's published API reference (codes and their classes:
retryable, execution unknown, budget exhausted, invalid request, authentication,
unavailable); a code the table does not know becomes a plain `GatewayError`, which
`calls.py` never retries. The stream parser reads the gateway's server-sent events and
stops at the first terminal event; a replayed call is one terminal event.

Resubmission keeps the old client's behaviour exactly: a retryable problem, a 5xx answer
without a problem body, a transport failure, and a stream that ends without its final
event are all resubmitted **with the same call ID**, waiting `Retry-After` (at least 1 s,
with jitter), up to 4 attempts within the call's deadline; then the last error is raised
to `calls.py`. The same ID is safe here because the gateway runs a call ID at most once:
it replays a finished call, answers `call_in_progress` while it is still running, and
runs it only if it never arrived. A new ID is used only where `calls.py` decides so today
(`execution_unknown`, `call_result_expired`, `idempotency_conflict`). The old client's
concurrency limiter is dropped: the agent runs one generation at a time.

Rejected: generating a client from the OpenAPI document (more code than two endpoints
need, and generated code still needs review); vendoring the old client's source (still
gateway code in the project, and its `Responses`/`Embeddings`/`OCR` surface is unused);
treating a broken connection as an unknown outcome with a new call ID (the gateway may
already have run the call, so a new ID could run and charge it twice).

### 2. Call behaviour does not change

`calls.py` keeps its call-ID scheme (`pa:{call_key}:{purpose}:{generation}:a{attempt}`),
`MAX_ATTEMPTS`, the running-call wait for `call_in_progress`, and the content-free
`model_call` records; only its imports change. The `conversation` spec's exactly-once
scenarios are the regression gate.

### 3. Tests through the API only

- **Client tests:** an `httpx2` mock transport answers with responses recorded from
  gateway 0.13.0 with synthetic adapters (`tests/fixtures/gateway/`, recorded as the
  playground's were: the published image, a throwaway database, the stub model): a
  streamed completion, a replay, `concurrency_exhausted` with `Retry-After`,
  `execution_unknown`, `budget_exhausted`, `call_result_expired`, `invalid_model_settings`,
  an invalid key, and a stream cut before its final event; resubmission tests count the
  requests and check that every one carries the same call ID.
- **Flow and script tests:** unchanged in intent; `tests/fakes.py` and `tests/scripted.py`
  build events from the agent's own types.
- **Contract test (opt-in, CI later):** `PA_CONTRACT_IMAGE=<digest>` starts the published
  gateway image with a throwaway Postgres, registers a project and a `reply_stub` profile
  on the stub model through the image's admin CLI, and runs the real reply request
  (`build_reply_request`) through the client; every field the recordings rely on must
  still be present. It replaces `tests/test_gateway_contract.py`, which needed a running
  gateway and a development key in `.env`.
- `./scripts/converse` keeps running scripted conversations on a gateway's stub profile,
  now through the agent's client.

Rejected: keeping the contract test on a locally running gateway (it depends on whichever
gateway version the laptop happens to run, 0.11.0 today).

## Risks

- **API drift:** a gateway change the recordings do not show would surface only in the
  contract test against a newer published image; the client pins the endpoints it uses
  and the contract test runs the image named by digest in CI once CI exists (next change).
- **Error-table drift:** a new gateway code is treated as non-retryable until the table is
  updated, which fails safe (an apology, never a double charge).

## Rollback

Revert the merge: the change has no data or setting changes.
