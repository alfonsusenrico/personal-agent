## ADDED Requirements

### Requirement: The agent reaches the gateway only through its HTTP API

The agent SHALL call the gateway only through the gateway's public HTTP API with its own
client and its project key, and SHALL build, test, and run without any gateway code,
package, or repository access.

#### Scenario: Clean build

- **WHEN** the agent is installed and its image built on a machine with no access to the
  gateway's repository
- **THEN** the install, the image build, and the default test suite succeed

### Requirement: Each gateway outcome keeps its meaning for the turn

The agent SHALL resubmit the same call ID after a retryable refusal, a broken connection,
or a stream that ends early, waiting as the gateway asks, up to 4 attempts; SHALL never
resubmit a call ID whose outcome is unknown (it uses a new ID for at most one more
attempt); SHALL send the budget notice when the budget is exhausted; and SHALL treat a
problem code it does not know as final for that call.

#### Scenario: Busy gateway

- **WHEN** the gateway refuses a reply call with `concurrency_exhausted` and a
  `Retry-After` of 1 second
- **THEN** the agent resubmits the same call ID after waiting, and the owner gets one reply

#### Scenario: Unknown outcome

- **WHEN** a reply call ends as `execution_unknown`
- **THEN** that call ID is never sent again, and the next attempt uses a new call ID

#### Scenario: Connection lost while the reply streams

- **WHEN** the connection to the gateway breaks after part of the reply was streamed
- **THEN** the agent sends the same call ID again, the gateway returns that call's result
  without running it again, and the owner gets one reply and is charged once

#### Scenario: Unknown problem code

- **WHEN** the gateway answers with a problem code the agent's table does not contain
- **THEN** the agent does not retry that call, records it as failed, and sends the apology

### Requirement: The API contract the agent relies on is tested

The agent's tests SHALL check its client against responses recorded from a gateway
release, and an opt-in contract test SHALL run the agent's real reply request against the
published gateway image, pinned by digest, and fail when a field the recordings rely on is
missing.

#### Scenario: Gateway field removed

- **WHEN** a newer gateway image no longer returns a field the recordings contain
- **THEN** the contract test against that image fails and names the missing field
