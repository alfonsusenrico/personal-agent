from __future__ import annotations

import asyncio

import pytest

from personal_agent.telegram.typing import TYPING_INTERVAL_SECONDS, TypingIndicator
from tests.fakes import FakeTelegramServer

pytestmark = pytest.mark.anyio
# Telegram shows a typing status for five seconds or less.
TYPING_VISIBLE_SECONDS = 5.0


async def test_typing_stays_visible_through_a_slow_reply() -> None:
    server = FakeTelegramServer()
    elapsed = 0.0
    sent_at: list[float] = []

    async def virtual_sleep(seconds: float) -> None:
        nonlocal elapsed
        sent_at.append(elapsed)
        elapsed += seconds
        await asyncio.sleep(0)

    typing = TypingIndicator(server.api(), sleep=virtual_sleep)
    async with typing("1001"):
        while elapsed < 12:
            await asyncio.sleep(0)

    # A 12-second reply: each status is refreshed before the previous one can fade.
    assert sent_at == [0.0, 4.0, 8.0]
    assert server.typing == 3
    assert TYPING_INTERVAL_SECONDS < TYPING_VISIBLE_SECONDS
    assert sent_at[-1] + TYPING_VISIBLE_SECONDS >= 12
