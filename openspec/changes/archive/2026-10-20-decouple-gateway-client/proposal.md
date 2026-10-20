# Proposal

## Why

The agent installs `llm-gateway-client` from the gateway's source repository
(`[tool.uv.sources]`, a git URL pinned to a commit). The gateway's repository became
private on 2026-10-20, so a clean `uv sync` or image build can no longer fetch it, and
the deployment change cannot build in CI. The owner's rule since then: projects and the
gateway are standalone, and a project talks to the gateway only through its HTTP API,
with its own thin client and tests against recorded responses or the published image.

## Expected Outcome

- The agent builds, tests, and runs with no gateway code, package, or repository access.
- The owner notices nothing in Telegram: replies stream as drafts, restarts answer each
  turn exactly once without paying twice, the budget notice and the apology appear
  exactly as before.
- The agent's own tests prove the part of the gateway API it relies on, including the
  certainty rule: an unknown outcome is never resubmitted under the same call ID.

## What Changes

- `personal_agent/gateway/api.py`: the agent's own client for the two endpoints it uses,
  `GET /v1/projects/self` (the startup check) and `POST /v1/responses` streamed with a
  profile; RFC 9457 problems mapped to the same error classes the conversation logic
  branches on today (retryable, execution unknown, budget exhausted, invalid request,
  authentication, unavailable; unknown codes are never retried).
- `gateway/calls.py`, `worker.py`, and the test doubles use it; `llm-gateway-client`
  and its git source leave `pyproject.toml` and `uv.lock`.
- Tests: client tests against responses recorded from gateway 0.13.0 (streams, a replay,
  each problem class, a stream that ends early); the existing flow and conversation-script
  tests keep their protocol-level fakes; the opt-in contract test starts the published
  gateway image (pinned by digest) with a throwaway database and a stub profile instead of
  needing a running gateway and a development key.
- `AGENTS.md` and `openspec/config.yaml`: the API-only rule replaces "llm-gateway-client
  for every model call".

## Non-goals

- Any change to replies, prompts, profiles, the call-ID scheme, or retry policy.
- A shared client package for other projects.
- Deployment (the next change, `adopt-deployment-standard`).

## Capabilities

### New Capabilities

- `gateway-access`: how the agent calls the gateway and what each outcome means for the
  turn.

### Modified Capabilities

None (the `conversation` requirements stay as they are and keep passing).

### Project capabilities and standards

None added (`llm` is already declared). No new model calls: the cost per message is
unchanged.

## Impact

Application code (`gateway/`, `worker.py`), test doubles, dependencies, docs. No data,
setting, or migration changes in the agent.

Found while planning: the `personal_agent` project's gateway contract is still version 1 on
both gateways, which the gateway refuses (`contract_upgrade_required`), so the agent cannot
call either gateway until the owner approves the gateway's own upgrade command (task 2.3
here for the laptop; the deployment change for production).
