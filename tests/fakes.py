"""Test doubles: a fake Telegram server behind the real client, a scriptable gateway,
and a controllable clock."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from llm_gateway_client import (
    CallResult,
    ExecutionUnknownError,
    GatewayError,
    InvalidRequestError,
    Usage,
)
from pydantic import SecretStr

from personal_agent.telegram.api import TelegramApi

BOT_TOKEN = "123456:TEST-bot-token-never-logged"
OWNER_ID = 1001
START = datetime(2026, 10, 13, 7, 5, tzinfo=UTC)
STRANGER_ID = 2002


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeTelegramServer:
    """Answers Bot API requests the way Telegram does, from in-memory state."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.typing = 0
        self.send_failures: list[httpx2.Response] = []
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            offset = body.get("offset")
            if offset is not None:
                # Calling with an offset confirms every update below it.
                self.updates = [update for update in self.updates if update["update_id"] >= offset]
            return _ok(list(self.updates))
        if method == "sendMessage":
            if self.send_failures:
                return self.send_failures.pop(0)
            self.sent.append(body)
            return _ok({"message_id": 5000 + len(self.sent)})
        if method == "sendChatAction":
            self.typing += 1
            return _ok(True)
        if method == "getMe":
            return _ok({"id": 42, "is_bot": True})
        return httpx2.Response(404, json={"ok": False, "error_code": 404})

    def api(self) -> TelegramApi:
        return TelegramApi(
            SecretStr(BOT_TOKEN),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)),
        )

    @property
    def sent_texts(self) -> list[str]:
        return [message["text"] for message in self.sent]


def _ok(result: object) -> httpx2.Response:
    return httpx2.Response(200, json={"ok": True, "result": result})


def rate_limited(retry_after: int) -> httpx2.Response:
    return httpx2.Response(
        429,
        json={
            "ok": False,
            "error_code": 429,
            "description": f"Too Many Requests: retry after {retry_after}",
            "parameters": {"retry_after": retry_after},
        },
    )


def message_update(
    update_id: int,
    text: str | None = None,
    *,
    user_id: int = OWNER_ID,
    chat_type: str = "private",
    message_id: int | None = None,
    date: datetime | None = None,
    edit_date: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Telegram update; `edit_date` makes it an edited_message."""
    moment = date or START
    message: dict[str, Any] = {
        "message_id": message_id or update_id,
        "date": int(moment.timestamp()),
        "chat": {"id": user_id if chat_type == "private" else -100500, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
    }
    if text is not None:
        message["text"] = text
    if extra:
        message.update(extra)
    if edit_date is not None:
        message["edit_date"] = int(edit_date.timestamp())
        return {"update_id": update_id, "edited_message": message}
    return {"update_id": update_id, "message": message}


OnCall = Callable[[dict[str, Any]], Awaitable[None]]


class FakeResponses:
    """The gateway's call contract: replay by call ID, conflict on a changed body,
    and execution_unknown stays unknown for that ID."""

    def __init__(self) -> None:
        self.script: list[str | GatewayError] = []
        self.default_reply = "Okay."
        self.calls: list[dict[str, Any]] = []
        self.on_call: OnCall | None = None
        self._finished: dict[str, tuple[str, CallResult]] = {}
        self._unknown: set[str] = set()

    async def create(
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
    ) -> CallResult:
        body = {
            "provider_call_id": provider_call_id,
            "operation": operation,
            "input": input,
            "profile": profile,
            "instructions": instructions,
            "max_output_tokens": max_output_tokens,
            "reasoning_effort": reasoning_effort,
            "prompt_cache_key": prompt_cache_key,
        }
        self.calls.append(body)
        fingerprint = json.dumps(body, sort_keys=True, default=str)
        if provider_call_id in self._unknown:
            raise ExecutionUnknownError("execution_unknown", status=502)
        if provider_call_id in self._finished:
            recorded, result = self._finished[provider_call_id]
            if recorded != fingerprint:
                raise InvalidRequestError("idempotency_conflict", status=409)
            return result.model_copy(update={"replayed": True})
        if self.on_call is not None:
            await self.on_call(body)
        outcome = self.script.pop(0) if self.script else self.default_reply
        if isinstance(outcome, ExecutionUnknownError):
            self._unknown.add(provider_call_id)
        if isinstance(outcome, GatewayError):
            raise outcome
        result = CallResult(
            provider_call_id=provider_call_id,
            status="completed",
            provider="stub",
            model="stub-echo",
            output_text=outcome,
            usage=Usage(input_tokens=120, cached_input_tokens=40, output_tokens=12),
            cost_usd="0.000150",
        )
        self._finished[provider_call_id] = (fingerprint, result)
        return result

    def developer_note(self, index: int = -1) -> str:
        items = self.calls[index]["input"]
        return next(item["content"] for item in items if item["role"] == "developer")

    def user_content(self, index: int = -1) -> str:
        return self.calls[index]["input"][-1]["content"]


@asynccontextmanager
async def no_typing(_chat_id: str) -> AsyncIterator[None]:
    yield
