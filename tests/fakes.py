"""Test doubles: a fake Telegram server behind the real client, a scriptable gateway,
and a controllable clock."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from llm_gateway_client import (
    CallResult,
    Completed,
    ExecutionUnknownError,
    GatewayError,
    InvalidRequestError,
    Started,
    StreamEvent,
    TextDelta,
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

    # Methods whose successful requests are kept in `sent`, in order.
    SENDING = frozenset({"sendMessage", "sendRichMessage", "setMessageReaction", "sendPoll"})

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.drafts: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.callback_answers: list[dict[str, Any]] = []
        # Responses to return instead of success, per method, in order.
        self.failures: dict[str, list[httpx2.Response]] = {}
        self.requests: list[httpx2.Request] = []
        self._next_message_id = 5000

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        if self.failures.get(method):
            return self.failures[method].pop(0)
        if method == "getUpdates":
            offset = body.get("offset")
            if offset is not None:
                # Calling with an offset confirms every update below it.
                self.updates = [update for update in self.updates if update["update_id"] >= offset]
            return _ok(list(self.updates))
        if method in self.SENDING:
            self.sent.append({"method": method, **body})
            if method == "setMessageReaction":
                return _ok(True)
            self._next_message_id += 1
            result: dict[str, Any] = {"message_id": self._next_message_id}
            if method == "sendPoll":
                result["poll"] = {"id": f"poll-{self._next_message_id}"}
            return _ok(result)
        if method in ("sendRichMessageDraft", "sendMessageDraft"):
            self.drafts.append({"method": method, **body})
            return _ok(True)
        if method == "editMessageText":
            self.edits.append(body)
            return _ok({"message_id": body["message_id"]})
        if method == "answerCallbackQuery":
            self.callback_answers.append(body)
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
    def messages(self) -> list[dict[str, Any]]:
        return [item for item in self.sent if item["method"] in ("sendMessage", "sendRichMessage")]

    @property
    def sent_texts(self) -> list[str]:
        """The text of each sent message: rich Markdown or plain text."""
        return [
            item["rich_message"]["markdown"]
            if item["method"] == "sendRichMessage"
            else item["text"]
            for item in self.messages
        ]


def bad_request(description: str) -> httpx2.Response:
    return httpx2.Response(400, json={"ok": False, "error_code": 400, "description": description})


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
    ) -> AsyncIterator[StreamEvent]:
        """Like the gateway: the text in pieces, then the final result; a replay is
        the final result alone."""
        result, replayed = await self._run(
            provider_call_id=provider_call_id,
            operation=operation,
            input=input,
            profile=profile,
            instructions=instructions,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            prompt_cache_key=prompt_cache_key,
        )
        if not replayed:
            yield Started(provider_call_id=provider_call_id, provider="stub", model="stub-echo")
            text = result.output_text or ""
            size = max(1, len(text) // 3)
            for start in range(0, len(text), size):
                yield TextDelta(delta=text[start : start + size])
                # A point where a Stop can cancel the call, as a network read would be.
                await asyncio.sleep(0)
        yield Completed(result=result)

    async def _run(self, **body: Any) -> tuple[CallResult, bool]:
        provider_call_id = body["provider_call_id"]
        body = dict(body)
        self.calls.append(body)
        fingerprint = json.dumps(body, sort_keys=True, default=str)
        if provider_call_id in self._unknown:
            raise ExecutionUnknownError("execution_unknown", status=502)
        if provider_call_id in self._finished:
            recorded, result = self._finished[provider_call_id]
            if recorded != fingerprint:
                raise InvalidRequestError("idempotency_conflict", status=409)
            return result.model_copy(update={"replayed": True}), True
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
        return result, False

    def developer_note(self, index: int = -1) -> str:
        items = self.calls[index]["input"]
        return next(item["content"] for item in items if item["role"] == "developer")

    def user_content(self, index: int = -1) -> str:
        return self.calls[index]["input"][-1]["content"]


class RecordingDrafts:
    """Stands in for Telegram drafts: keeps every text a draft was given."""

    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []
        self.opened: list[tuple[str, int]] = []

    @asynccontextmanager
    async def __call__(self, chat_id: str, draft_id: int) -> AsyncIterator[RecordingDrafts.Sink]:
        self.opened.append((chat_id, draft_id))
        yield RecordingDrafts.Sink(self, draft_id)

    class Sink:
        def __init__(self, owner: RecordingDrafts, draft_id: int) -> None:
            self._owner = owner
            self._draft_id = draft_id

        def update(self, text: str) -> None:
            self._owner.updates.append((self._draft_id, text))


def button_press(
    update_id: int, token: str, *, message_id: int, user_id: int = OWNER_ID
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq-{update_id}",
            "from": {"id": user_id, "is_bot": False},
            "message": {
                "message_id": message_id,
                "date": 0,
                "chat": {"id": user_id, "type": "private"},
            },
            "data": token,
        },
    }


def poll_vote(update_id: int, poll_id: str, option_ids: list[int]) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "poll_answer": {
            "poll_id": poll_id,
            "user": {"id": OWNER_ID, "is_bot": False},
            "option_ids": option_ids,
        },
    }


def stop_press(update_id: int, draft_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "stopped_message_generation": {
            "chat": {"id": OWNER_ID, "type": "private"},
            "draft_id": draft_id,
        },
    }
