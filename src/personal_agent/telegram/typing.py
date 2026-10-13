"""Keep Telegram's typing status visible while a reply is prepared."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from personal_agent.telegram.api import TelegramApi, TelegramError

# Telegram shows the status for five seconds or less, so it is refreshed sooner.
TYPING_INTERVAL_SECONDS = 4.0
logger = logging.getLogger(__name__)


class TypingIndicator:
    def __init__(
        self,
        telegram: TelegramApi,
        *,
        interval_seconds: float = TYPING_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._telegram = telegram
        self._interval = interval_seconds
        self._sleep = sleep

    @contextlib.asynccontextmanager
    async def __call__(self, chat_id: str) -> AsyncIterator[None]:
        task = asyncio.create_task(self._refresh(chat_id))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _refresh(self, chat_id: str) -> None:
        while True:
            try:
                await self._telegram.send_typing(chat_id=chat_id)
            except TelegramError as exc:
                # Cosmetic: a missed typing status never delays the reply.
                logger.debug("Typing status failed: %s", exc.code)
            await self._sleep(self._interval)
