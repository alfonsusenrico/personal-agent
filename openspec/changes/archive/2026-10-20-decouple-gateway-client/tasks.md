# Tasks

## 1. Client

- [x] 1.1 `gateway/api.py`: `GatewayApi` (`whoami`, streamed `responses`), event types,
  problem-code table and error classes, same-ID resubmission with the old client's limits
  (4 attempts, `Retry-After`, jitter, deadline). Verify: client tests against the recorded
  responses, including each problem class, a cut stream, and a broken connection, with the
  number of requests and their call IDs checked.
- [x] 1.2 Record the responses from gateway 0.13.0 (published image, throwaway database,
  stub model, a `reply_stub` profile) into `tests/fixtures/gateway/` with a README. Verify:
  the fixtures contain no keys (grep) and the client tests pass on them.

Expected Result: the agent has a tested client for the two endpoints it uses.

## 2. Switch over

- [x] 2.1 `gateway/calls.py`, `worker.py`, `tests/fakes.py`, `tests/scripted.py`,
  `tests/converse.py` use the agent's client and types; `llm-gateway-client` and its git
  source leave `pyproject.toml` and `uv.lock`. Verify: `./scripts/check` with
  `REQUIRE_DB=1` (every conversation and flow test, including the exactly-once restart
  scenarios); `uv tree` has no gateway package; a clean `docker build` with no gateway
  access.
- [x] 2.2 Contract test against the published gateway image replaces
  `tests/test_gateway_contract.py`. Verify: it passes against 0.13.0
  (`sha256:2e24e95b…`) and fails with a named field when a recorded field is removed from
  its expectations (checked once by hand).
- [x] 2.3 Owner-approved: append the alias-backed contract for `personal_agent` on the
  laptop's development gateway (`llm-gateway-admin project upgrade-contract
  personal_agent`; earlier versions and keys stay). Its contract is still version 1, which
  the gateway refuses with `contract_upgrade_required`. Then `./scripts/converse` through
  the agent's client on the `reply_stub` profile. Verify: the exported contract is version
  2 with the same profiles mapped to their aliases; every conversation script passes (free;
  no paid call).
- [x] 2.4 `AGENTS.md`, `openspec/config.yaml`, README: the API-only rule and the new test
  commands. Verify: `openspec validate --all --strict`.

Expected Result: the agent builds and runs with no gateway code; behaviour is unchanged.

## 3. Delivery

- [x] 3.1 `verification.md` (revision, commands and counts, what was not verified); push
  the branch, open the pull request, report its link; the owner merges.
