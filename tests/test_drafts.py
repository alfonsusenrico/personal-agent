from __future__ import annotations

import asyncio

import pytest

from personal_agent.telegram.drafts import Drafts
from tests.fakes import FakeTelegramServer, bad_request

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


async def test_placeholder_then_changing_text_then_refresh() -> None:
    server = FakeTelegramServer()
    time = _VirtualTime()
    drafts = Drafts(server.api(), interval_seconds=1.0, sleep=time.sleep, monotonic=time.monotonic)

    async with drafts("1001", 7) as draft:
        await _run_until(time, 2)
        draft.update("Hel")
        draft.update('Hello <pa-react emoji="👍"/> there')
        await _run_until(time, 4)
        await _run_until(time, 26)

    shown = [
        (item["method"], item.get("rich_message", {}).get("markdown")) for item in server.drafts
    ]
    assert shown[0] == ("sendMessageDraft", None)
    assert shown[1] == ("sendRichMessageDraft", "Hello  there")
    # Unchanged text is sent again only to keep the 30-second draft alive.
    assert len(shown) == 3
    assert {item["draft_id"] for item in server.drafts} == {7}


async def test_rejected_drafts_never_raise() -> None:
    server = FakeTelegramServer()
    server.failures["sendMessageDraft"] = [bad_request("Bad Request: draft")]
    time = _VirtualTime()
    drafts = Drafts(server.api(), sleep=time.sleep, monotonic=time.monotonic)

    async with drafts("1001", 7) as draft:
        draft.update("text")
        await _run_until(time, 3)

    assert server.drafts[-1]["rich_message"]["markdown"] == "text"
