from __future__ import annotations

import json
import logging

import httpx2
import pytest
from pydantic import SecretStr

from personal_agent.logging_setup import quiet_url_logging
from personal_agent.telegram.api import (
    TelegramApi,
    TelegramConflict,
    TelegramError,
    TelegramRateLimited,
)
from tests.fakes import BOT_TOKEN, rate_limited

pytestmark = pytest.mark.anyio


def _api(handler: httpx2.MockTransport) -> TelegramApi:
    return TelegramApi(SecretStr(BOT_TOKEN), http_client=httpx2.AsyncClient(transport=handler))


async def test_get_updates_long_polls_messages_and_edits_from_the_offset() -> None:
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(200, json={"ok": True, "result": [{"update_id": 7}]})

    updates = await _api(httpx2.MockTransport(handler)).get_updates(offset=7, timeout_seconds=30)

    assert updates == [{"update_id": 7}]
    assert captured[0].url.path == f"/bot{BOT_TOKEN}/getUpdates"
    assert json.loads(captured[0].content) == {
        "timeout": 30,
        "allowed_updates": ["message", "edited_message"],
        "offset": 7,
    }


async def test_send_message_replies_only_when_asked() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 99}})

    api = _api(httpx2.MockTransport(handler))
    first = await api.send_message(chat_id="1001", text="hi")
    await api.send_message(chat_id="1001", text="re", reply_to_message_id="12")

    assert first == "99"
    assert bodies[0] == {"chat_id": "1001", "text": "hi"}
    assert bodies[1]["reply_parameters"] == {"message_id": 12, "allow_sending_without_reply": True}


async def test_flood_control_carries_retry_after() -> None:
    api = _api(httpx2.MockTransport(lambda _request: rate_limited(3)))

    with pytest.raises(TelegramRateLimited) as raised:
        await api.send_message(chat_id="1001", text="hi")

    assert raised.value.retry_after_seconds == 3.0


async def test_conflict_and_permanent_errors_are_typed() -> None:
    conflict = _api(httpx2.MockTransport(lambda _r: httpx2.Response(409, json={"ok": False})))
    forbidden = _api(httpx2.MockTransport(lambda _r: httpx2.Response(403, json={"ok": False})))

    with pytest.raises(TelegramConflict):
        await conflict.get_updates(offset=None, timeout_seconds=1)
    with pytest.raises(TelegramError) as raised:
        await forbidden.send_message(chat_id="1001", text="hi")

    assert raised.value.code == "http_403"
    assert raised.value.permanent


async def test_errors_and_logs_never_contain_the_token(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("failed", request=request)

    quiet_url_logging()
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TelegramError) as raised:
        await _api(httpx2.MockTransport(handler)).send_typing(chat_id="1001")

    assert raised.value.code == "ConnectError"
    assert raised.value.__cause__ is None
    assert BOT_TOKEN not in str(raised.value)
    assert BOT_TOKEN not in caplog.text
