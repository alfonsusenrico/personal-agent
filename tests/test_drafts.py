from __future__ import annotations

import asyncio

import pytest

from personal_agent.telegram.drafts import Drafts
from tests.fakes import FakeTelegramServer, bad_request, rate_limited

pytestmark = pytest.mark.anyio


class _VirtualTime:
    def __init__(self) -> None:
        self.now = 0.0

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)

    def monotonic(self) -> float:
        return self.now


async def _run_until(time: _VirtualTime, seconds: float) -> None:
    while time.now < seconds:
        await asyncio.sleep(0)


async def test_typing_until_text_then_changing_text_then_refresh() -> None:
    server = FakeTelegramServer()
    time = _VirtualTime()
    drafts = Drafts(server.api(), interval_seconds=1.0, sleep=time.sleep, monotonic=time.monotonic)

    async with drafts("1001", 7) as draft:
        await _run_until(time, 9)
        draft.update("Hel")
        draft.update('Hello <pa-react emoji="👍"/> there')
        await _run_until(time, 11)
        await _run_until(time, 33)

    # Thinking shows as the typing status, refreshed before it fades after 5 seconds.
    assert server.typing == 3
    shown = [
        (item["method"], item.get("rich_message", {}).get("markdown")) for item in server.drafts
    ]
    assert shown[0] == ("sendRichMessageDraft", "Hello  there")
    # Unchanged text is sent again only to keep the 30-second draft alive.
    assert len(shown) == 2
    assert {item["draft_id"] for item in server.drafts} == {7}
    assert all(item["can_stop"] for item in server.drafts)


async def test_a_reply_without_text_never_opens_a_draft() -> None:
    server = FakeTelegramServer()
    time = _VirtualTime()
    drafts = Drafts(server.api(), sleep=time.sleep, monotonic=time.monotonic)

    async with drafts("1001", 7) as draft:
        draft.update('<pa-react emoji="👍"/>')
        await _run_until(time, 3)

    assert server.drafts == []
    assert server.typing == 1


async def test_rejected_drafts_never_raise() -> None:
    server = FakeTelegramServer()
    server.failures["sendChatAction"] = [bad_request("Bad Request: typing")]
    server.failures["sendRichMessageDraft"] = [bad_request("Bad Request: draft")]
    time = _VirtualTime()
    drafts = Drafts(server.api(), sleep=time.sleep, monotonic=time.monotonic)

    async with drafts("1001", 7) as draft:
        await _run_until(time, 1)
        draft.update("text")
        await _run_until(time, 2)
        draft.update("text and more")
        await _run_until(time, 4)

    assert server.failures == {"sendChatAction": [], "sendRichMessageDraft": []}
    assert [item["rich_message"]["markdown"] for item in server.drafts] == ["text and more"]


async def test_flood_control_waits_then_sends_the_same_text() -> None:
    server = FakeTelegramServer()
    server.failures["sendRichMessageDraft"] = [rate_limited(3)]
    time = _VirtualTime()
    drafts = Drafts(server.api(), sleep=time.sleep, monotonic=time.monotonic)

    async with drafts("1001", 7) as draft:
        draft.update("text")
        await _run_until(time, 6)

    assert [item["rich_message"]["markdown"] for item in server.drafts] == ["text"]
