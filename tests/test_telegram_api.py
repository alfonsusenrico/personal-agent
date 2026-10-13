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
        "allowed_updates": [
            "message",
            "edited_message",
            "callback_query",
            "poll_answer",
            "stopped_message_generation",
        ],
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
        await _api(httpx2.MockTransport(handler)).send_draft(
            chat_id="1001", draft_id=7, markdown=""
        )

    assert raised.value.code == "ConnectError"
    assert raised.value.__cause__ is None
    assert BOT_TOKEN not in str(raised.value)
    assert BOT_TOKEN not in caplog.text


def _capture() -> tuple[list[tuple[str, dict[str, object]]], httpx2.MockTransport]:
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append((method, json.loads(request.content)))
        result: object = (
            {"message_id": 9, "poll": {"id": "p1"}} if method != "setMessageReaction" else True
        )
        return httpx2.Response(200, json={"ok": True, "result": result})

    return calls, httpx2.MockTransport(handler)


async def test_rich_message_carries_markdown_reply_and_effect() -> None:
    calls, transport = _capture()

    message_id = await _api(transport).send_rich_message(
        chat_id="1001", markdown="**hi**", reply_to_message_id="4", effect_id="123"
    )

    assert message_id == "9"
    assert calls == [
        (
            "sendRichMessage",
            {
                "chat_id": "1001",
                "rich_message": {"markdown": "**hi**"},
                "reply_parameters": {"message_id": 4, "allow_sending_without_reply": True},
                "message_effect_id": "123",
            },
        )
    ]


async def test_empty_draft_is_the_thinking_placeholder_with_stop() -> None:
    calls, transport = _capture()
    api = _api(transport)

    await api.send_draft(chat_id="1001", draft_id=7, markdown="")
    await api.send_draft(chat_id="1001", draft_id=7, markdown="So far")

    assert calls == [
        ("sendMessageDraft", {"chat_id": 1001, "draft_id": 7, "text": "", "can_stop": True}),
        (
            "sendRichMessageDraft",
            {
                "chat_id": 1001,
                "draft_id": 7,
                "rich_message": {"markdown": "So far"},
                "can_stop": True,
            },
        ),
    ]


async def test_poll_is_not_anonymous_and_returns_its_ids() -> None:
    calls, transport = _capture()

    ids = await _api(transport).send_poll(
        chat_id="1001", question="Dinner?", options=["Pizza", "Sushi"], allows_multiple=False
    )

    assert ids == ("9", "p1")
    assert calls[0][1]["is_anonymous"] is False
    assert calls[0][1]["options"] == [{"text": "Pizza"}, {"text": "Sushi"}]


async def test_reaction_edit_and_callback_bodies() -> None:
    calls, transport = _capture()
    api = _api(transport)

    await api.set_reaction(chat_id="1001", message_id="5", emoji="👍")
    await api.edit_rich_message(chat_id="1001", message_id="9", markdown="✓ A")
    await api.answer_callback(callback_query_id="q1", text="gone")

    assert calls == [
        (
            "setMessageReaction",
            {"chat_id": "1001", "message_id": 5, "reaction": [{"type": "emoji", "emoji": "👍"}]},
        ),
        (
            "editMessageText",
            {"chat_id": "1001", "message_id": 9, "rich_message": {"markdown": "✓ A"}},
        ),
        ("answerCallbackQuery", {"callback_query_id": "q1", "text": "gone"}),
    ]


async def test_rejected_effect_is_classified_without_its_description() -> None:
    api = _api(
        httpx2.MockTransport(
            lambda _r: httpx2.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: EFFECT_ID_INVALID",
                },
            )
        )
    )

    with pytest.raises(TelegramError) as raised:
        await api.send_rich_message(chat_id="1001", markdown="hi", effect_id="1")

    assert raised.value.code == "effect_invalid"
    assert "EFFECT_ID_INVALID" not in str(raised.value)
