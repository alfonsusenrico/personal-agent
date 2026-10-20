"""The agent's gateway client against responses recorded from a gateway release."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

from personal_agent.gateway.api import (
    AuthenticationError,
    BudgetExhaustedError,
    Completed,
    ExecutionUnknownError,
    GatewayClient,
    GatewayError,
    GatewayUnavailableError,
    InvalidRequestError,
    RetryableError,
    Started,
    StreamEvent,
    TextDelta,
)

pytestmark = pytest.mark.anyio
FIXTURES = Path(__file__).parent / "fixtures" / "gateway"
KEY = "lgw_dev_synthetic_key_never_sent_anywhere"
CALL_ID = "pa:7c1e:reply:1:a1"


def recorded(name: str) -> httpx2.Response:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    if "text" in data:
        return httpx2.Response(
            data["status"],
            headers={**data["headers"], "content-type": "text/event-stream"},
            content=data["text"].encode(),
        )
    return httpx2.Response(data["status"], headers=data["headers"], json=data["body"])


def cut(name: str) -> httpx2.Response:
    """A recorded stream that ends before its final event, as a dropped connection would."""
    text = json.loads((FIXTURES / f"{name}.json").read_text())["text"]
    kept = text[: text.index("event: response.completed")]
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=kept.encode()
    )


class Gateway:
    """Answers each request with the next scripted response and records what it got."""

    def __init__(self, *answers: httpx2.Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[httpx2.Request] = []
        self.waits: list[float] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client(self) -> GatewayClient:
        async def sleep(seconds: float) -> None:
            self.waits.append(seconds)

        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))
        return GatewayClient("http://gateway.test/", KEY, http_client=http, sleep=sleep)

    def call_ids(self) -> list[str]:
        return [json.loads(request.content)["provider_call_id"] for request in self.requests]


async def stream(gateway: Gateway, **options: Any) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async with gateway.client() as client:
        async for event in client.responses.stream(
            provider_call_id=CALL_ID,
            operation="reply",
            input=[{"role": "user", "content": "Synthetic hello"}],
            profile="reply_stub",
            **options,
        ):
            events.append(event)
    return events


async def raised(gateway: Gateway) -> GatewayError:
    with pytest.raises(GatewayError) as caught:
        await stream(gateway)
    return caught.value


async def test_whoami_reads_the_project_with_the_key() -> None:
    gateway = Gateway(recorded("identity"))
    async with gateway.client() as client:
        identity = await client.whoami()
    assert (identity.project_id, identity.environment) == ("pa_record", "development")
    request = gateway.requests[0]
    assert (request.method, request.url.path) == ("GET", "/v1/projects/self")
    assert request.headers["authorization"] == f"Bearer {KEY}"


async def test_whoami_problems_and_an_unreachable_gateway() -> None:
    with pytest.raises(AuthenticationError) as caught:
        async with Gateway(recorded("problem--key-invalid")).client() as client:
            await client.whoami()
    assert caught.value.code == "key_malformed"
    with pytest.raises(RetryableError) as unreachable:
        async with Gateway(httpx2.ConnectError("refused")).client() as client:
            await client.whoami()
    assert unreachable.value.code == "gateway_unreachable"


async def test_a_stream_yields_started_text_and_the_result() -> None:
    gateway = Gateway(recorded("stream--completed"))
    events = await stream(gateway, max_output_tokens=200, prompt_cache_key="pa:1")
    assert isinstance(events[0], Started)
    assert "".join(event.delta for event in events if isinstance(event, TextDelta)) == (
        "stub: Synthetic hello"
    )
    final = events[-1]
    assert isinstance(final, Completed)
    result = final.result
    assert (result.status, result.model, result.cost_usd, result.replayed) == (
        "completed",
        "stub-echo",
        "0.00",
        False,
    )
    assert (result.usage.input_tokens, result.usage.output_tokens) == (59, 5)
    sent = json.loads(gateway.requests[0].content)
    assert sent == {
        "provider_call_id": CALL_ID,
        "operation": "reply",
        "input": [{"role": "user", "content": "Synthetic hello"}],
        "stream": True,
        "profile": "reply_stub",
        "max_output_tokens": 200,
        "prompt_cache_key": "pa:1",
    }


async def test_a_replay_is_the_final_result_alone() -> None:
    events = await stream(Gateway(recorded("stream--replayed")))
    assert len(events) == 1
    assert isinstance(events[0], Completed) and events[0].result.replayed


async def test_an_incomplete_answer_is_a_result_with_its_reason() -> None:
    events = await stream(Gateway(recorded("stream--incomplete")))
    final = events[-1]
    assert isinstance(final, Completed)
    assert (final.result.status, final.result.incomplete_reason) == (
        "incomplete",
        "max_output_tokens",
    )


@pytest.mark.parametrize(
    "first",
    [
        "problem--provider-rate-limited",
        "problem--concurrency-exhausted",
        "problem--call-in-progress",
    ],
)
async def test_a_retryable_refusal_is_resubmitted_with_the_same_call_id(first: str) -> None:
    gateway = Gateway(recorded(first), recorded("stream--completed"))
    events = await stream(gateway)
    assert isinstance(events[-1], Completed)
    assert gateway.call_ids() == [CALL_ID, CALL_ID]
    assert len(gateway.waits) == 1 and 1.0 <= gateway.waits[0] <= 1.2


@pytest.mark.parametrize(
    "broken",
    [
        lambda: cut("stream--completed"),
        lambda: httpx2.ReadError("connection reset"),
        lambda: httpx2.Response(502, text="Bad gateway"),
    ],
    ids=["cut stream", "lost connection", "5xx without a problem"],
)
async def test_a_broken_call_is_resubmitted_and_replays_the_result(
    broken: Callable[[], httpx2.Response | Exception],
) -> None:
    gateway = Gateway(broken(), recorded("stream--replayed"))
    events = await stream(gateway)
    final = events[-1]
    assert isinstance(final, Completed) and final.result.replayed
    assert final.result.output_text == "stub: Synthetic hello"
    assert gateway.call_ids() == [CALL_ID, CALL_ID]
    assert sum(isinstance(event, Started) for event in events) <= 1


async def test_resubmission_stops_after_four_attempts() -> None:
    gateway = Gateway(*(recorded("problem--concurrency-exhausted") for _ in range(4)))
    error = await raised(gateway)
    assert isinstance(error, RetryableError) and error.code == "concurrency_exhausted"
    assert gateway.call_ids() == [CALL_ID] * 4
    assert len(gateway.waits) == 3


async def test_a_wait_past_the_deadline_goes_to_the_caller() -> None:
    late = httpx2.Response(
        429,
        headers={"retry-after": "300"},
        json={"code": "concurrency_exhausted", "status": 429, "title": "busy"},
    )
    gateway = Gateway(late)
    error = await raised(gateway)
    assert isinstance(error, RetryableError)
    assert (len(gateway.requests), gateway.waits) == (1, [])


@pytest.mark.parametrize(
    "name,kind,code",
    [
        ("problem--execution-unknown", ExecutionUnknownError, "execution_unknown"),
        ("stream--fail-mid-stream", ExecutionUnknownError, "execution_unknown"),
        ("problem--budget-exhausted", BudgetExhaustedError, "budget_exhausted"),
        ("problem--call-result-expired", GatewayUnavailableError, "call_result_expired"),
        ("problem--profile-not-found", InvalidRequestError, "profile_not_found"),
    ],
)
async def test_final_outcomes_are_never_resubmitted(
    name: str, kind: type[GatewayError], code: str
) -> None:
    gateway = Gateway(recorded(name))
    error = await raised(gateway)
    assert type(error) is kind and error.code == code
    assert len(gateway.requests) == 1 and gateway.waits == []


async def test_the_budget_refusal_carries_the_wait_until_next_month() -> None:
    error = await raised(Gateway(recorded("problem--budget-exhausted")))
    assert error.retry_after_seconds == 950400


async def test_an_unknown_code_is_final_and_unclassified() -> None:
    problem = {"code": "brand_new_code", "status": 503, "title": "New"}
    gateway = Gateway(httpx2.Response(503, json=problem))
    error = await raised(gateway)
    assert type(error) is GatewayError and error.code == "brand_new_code"
    assert len(gateway.requests) == 1


async def test_a_client_error_without_a_problem_is_not_resubmitted() -> None:
    gateway = Gateway(httpx2.Response(404, text="Not here"))
    error = await raised(gateway)
    assert type(error) is GatewayError and error.code == "unexpected_response"
    assert len(gateway.requests) == 1
