"""The agent's own client for the gateway's HTTP API; no gateway code is installed.

It covers what the agent uses: `GET /v1/projects/self` and streamed `POST /v1/responses`.
Problems map to error classes by code, as gateway 0.13.0 defines them; a code this table
does not know is a plain `GatewayError`, which callers never retry.

A call is resubmitted with the same call ID after a retryable problem, a 5xx answer
without a problem body, a lost connection, or a stream that ends before its final event.
That is safe because the gateway runs a call ID at most once: it replays a finished call,
answers `call_in_progress` while it runs, and runs it only if it never arrived.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from pydantic import BaseModel, ConfigDict

# Waiting and resubmitting the same call ID can succeed.
RETRYABLE_CODES = frozenset(
    {
        "provider_rate_limited",
        "provider_unavailable",
        "rate_limited",
        "concurrency_exhausted",
        "call_in_progress",
        "allocation_pending",
        "request_capacity",
    }
)
INVALID_REQUEST_CODES = frozenset(
    {
        "cost_ceiling_exceeded",
        "request_invalid",
        "request_too_large",
        "profile_not_found",
        "model_not_in_catalogue",
        "invalid_model_settings",
        "input_not_supported",
        "continuation_mismatch",
        "provider_invalid_request",
        "idempotency_conflict",
        "stub_not_allowed",
        "model_task_mismatch",
        "credential_changed",
        "call_configuration_changed",
        "not_found",
        "method_not_allowed",
    }
)
AUTHENTICATION_CODES = frozenset(
    {"key_missing", "key_malformed", "key_invalid", "project_disabled", "auth_throttled"}
)
UNAVAILABLE_CODES = frozenset(
    {
        "provider_account_blocked",
        "provider_not_configured",
        "provider_failed",
        "project_quarantined",
        "contract_upgrade_required",
        "call_result_expired",
        "price_unavailable",
        "accounting_policy_unavailable",
        "paid_admission_stopped",
        "provider_configuration_mismatch",
        "provider_credentials_missing",
        "provider_credentials_unreadable",
        "model_registry_unavailable",
        "dependency_unavailable",
        "internal_error",
    }
)
UNREACHABLE = "gateway_unreachable"
_UNREACHABLE_WAIT_SECONDS = 1.0
_TIMEOUT = httpx2.Timeout(240.0, connect=10.0)


class GatewayError(Exception):
    """A gateway problem. Branch on `code`, never on the message."""

    def __init__(
        self,
        code: str,
        *,
        status: int | None = None,
        title: str | None = None,
        detail: str | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.title = title
        self.detail = detail
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


class RetryableError(GatewayError):
    """Waiting `retry_after_seconds` and resubmitting the same call ID can succeed."""


class ExecutionUnknownError(GatewayError):
    """The provider may have run the call. Never resubmit this call ID; use a new one."""


class BudgetExhaustedError(GatewayError):
    """The monthly budget is spent; `retry_after_seconds` reaches the next month."""


class InvalidRequestError(GatewayError):
    """The request cannot succeed as sent."""


class AuthenticationError(GatewayError):
    """The key is missing, invalid, throttled, or its project is disabled."""


class GatewayUnavailableError(GatewayError):
    """The gateway or provider cannot serve this call, and waiting will not help."""


def error_from_problem(
    status: int, problem: dict[str, Any], retry_after: float | None
) -> GatewayError:
    code = str(problem.get("code") or "unknown")
    fields: dict[str, Any] = {
        "status": status,
        "title": problem.get("title"),
        "detail": problem.get("detail"),
        "request_id": problem.get("request_id"),
        "retry_after_seconds": retry_after,
    }
    if code in RETRYABLE_CODES:
        return RetryableError(code, **fields)
    if code == "execution_unknown":
        return ExecutionUnknownError(code, **fields)
    if code == "budget_exhausted":
        return BudgetExhaustedError(code, **fields)
    if code in INVALID_REQUEST_CODES:
        return InvalidRequestError(code, **fields)
    if code in AUTHENTICATION_CODES:
        return AuthenticationError(code, **fields)
    if code in UNAVAILABLE_CODES:
        return GatewayUnavailableError(code, **fields)
    return GatewayError(code, **fields)


class _Lenient(BaseModel):
    # Unknown fields are ignored, so a newer gateway stays compatible.
    model_config = ConfigDict(extra="ignore", frozen=True)


class Usage(_Lenient):
    input_tokens: int
    cached_input_tokens: int = 0
    output_tokens: int
    reasoning_tokens: int | None = None


class CallResult(_Lenient):
    provider_call_id: str
    status: Literal["completed", "incomplete"]
    provider: str
    model: str
    output_text: str | None = None
    refusal: str | None = None
    incomplete_reason: str | None = None
    usage: Usage
    cost_usd: str | None = None
    replayed: bool = False


class Identity(_Lenient):
    project_id: str
    display_name: str
    environment: str


class Started(_Lenient):
    """The provider accepted the streamed call."""

    provider_call_id: str
    provider: str
    model: str


class TextDelta(_Lenient):
    delta: str


class Completed(_Lenient):
    """The final answer; after a resubmission its text is the authoritative one."""

    result: CallResult


StreamEvent = Started | TextDelta | Completed


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How long one call may keep resubmitting before the error goes to the caller."""

    max_attempts: int = 4
    max_total_seconds: float = 90.0
    jitter: float = 0.2


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _problem(response: httpx2.Response) -> GatewayError:
    try:
        problem = response.json()
    except ValueError:
        problem = None
    if not isinstance(problem, dict) or "code" not in problem:
        if response.status_code >= 500:
            # No problem body: something between the agent and the gateway failed.
            return RetryableError(
                UNREACHABLE,
                status=response.status_code,
                retry_after_seconds=_UNREACHABLE_WAIT_SECONDS,
            )
        return GatewayError("unexpected_response", status=response.status_code)
    return error_from_problem(response.status_code, problem, _retry_after(response))


async def _sse_events(response: httpx2.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    name, data = "", ""
    async for line in response.aiter_lines():
        if not line:
            if name:
                yield name, json.loads(data or "{}")
            name, data = "", ""
        elif line.startswith(":"):
            continue  # keep-alive comment
        elif line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            data += line[len("data: ") :]


class Responses:
    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    async def stream(
        self,
        *,
        provider_call_id: str,
        operation: str,
        input: str | list[dict[str, Any]],
        profile: str | None = None,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        stub: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a call. If the connection drops, the same call ID is resubmitted and
        the stream ends with the replayed `Completed`, whose text is authoritative.

        `stub` scripts the gateway's free stub model (replies, failures); only the
        conversation scripts and tests set it."""
        body: dict[str, Any] = {
            "provider_call_id": provider_call_id,
            "operation": operation,
            "input": input,
            "stream": True,
        }
        options = {
            "profile": profile,
            "instructions": instructions,
            "max_output_tokens": max_output_tokens,
            "reasoning_effort": reasoning_effort,
            "prompt_cache_key": prompt_cache_key,
            "stub": stub,
        }
        body.update({key: value for key, value in options.items() if value is not None})
        async for event in self._client.stream_call(body):
            yield event


class GatewayClient:
    """One instance per process."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        retry_policy: RetryPolicy | None = None,
        http_client: httpx2.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http_client or httpx2.AsyncClient(timeout=_TIMEOUT)
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "personal-agent"}
        self._policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self.responses = Responses(self)

    async def __aenter__(self) -> GatewayClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def whoami(self) -> Identity:
        try:
            response = await self._http.get(
                f"{self._base_url}/v1/projects/self", headers=self._headers
            )
        except httpx2.TransportError:
            raise RetryableError(
                UNREACHABLE, retry_after_seconds=_UNREACHABLE_WAIT_SECONDS
            ) from None
        if response.status_code != 200:
            raise _problem(response)
        return Identity.model_validate(response.json())

    def _next_wait(self, error: GatewayError, attempt: int, deadline: float) -> float | None:
        """Seconds to wait before resubmitting, or None when the error goes to the caller."""
        if not isinstance(error, RetryableError):
            return None
        jitter = 1 + random.random() * self._policy.jitter  # noqa: S311  # jitter, not security
        wait = max(1.0, error.retry_after_seconds or 1.0) * jitter
        if attempt >= self._policy.max_attempts or time.monotonic() + wait > deadline:
            return None
        return wait

    async def stream_call(self, body: dict[str, Any]) -> AsyncIterator[StreamEvent]:
        deadline = time.monotonic() + self._policy.max_total_seconds
        attempt = 0
        started = False
        while True:
            attempt += 1
            error: GatewayError
            try:
                async with self._http.stream(
                    "POST", f"{self._base_url}/v1/responses", json=body, headers=self._headers
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        error = _problem(response)
                    else:
                        async for name, data in _sse_events(response):
                            if name == "response.started" and not started:
                                started = True
                                yield Started.model_validate(data)
                            elif name == "text.delta":
                                yield TextDelta.model_validate(data)
                            elif name == "response.completed":
                                yield Completed(result=CallResult.model_validate(data))
                                return
                            elif name == "error":
                                raise error_from_problem(
                                    int(data.get("status") or 502),
                                    data,
                                    data.get("retry_after_seconds"),
                                )
                        # The stream ended without its final event: resubmit the call ID.
                        error = RetryableError(
                            UNREACHABLE, retry_after_seconds=_UNREACHABLE_WAIT_SECONDS
                        )
            except httpx2.TransportError:
                # The gateway may have finished the call; resubmitting the same ID replays
                # it or reports call_in_progress, never a second execution.
                error = RetryableError(UNREACHABLE, retry_after_seconds=_UNREACHABLE_WAIT_SECONDS)
            wait = self._next_wait(error, attempt, deadline)
            if wait is None:
                raise error
            await self._sleep(wait)
