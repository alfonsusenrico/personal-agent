"""Show a reply as a live Telegram draft while it is written.

The first draft is Telegram's "Thinking…" placeholder with a Stop button; then the
text so far replaces it, at most once per interval and only when it changed. A
draft lasts 30 seconds, so an unchanged one is sent again before it fades. Drafts
are cosmetic: a failed update is logged by code and never delays the reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from personal_agent.telegram.api import TelegramApi, TelegramError, TelegramRateLimited
from personal_agent.telegram.rich import draft_view

DRAFT_INTERVAL_SECONDS = 1.0
# Telegram keeps a draft for 30 seconds; refresh well before it disappears.
DRAFT_REFRESH_SECONDS = 20.0
logger = logging.getLogger(__name__)


class DraftSink:
    """Receives the text written so far; the draft loop shows it."""

    def __init__(self) -> None:
        self.text = ""

    def update(self, text: str) -> None:
        self.text = text


class Drafts:
    def __init__(
        self,
        telegram: TelegramApi,
        *,
        interval_seconds: float = DRAFT_INTERVAL_SECONDS,
        refresh_seconds: float = DRAFT_REFRESH_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._telegram = telegram
        self._interval = interval_seconds
        self._refresh = refresh_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    @contextlib.asynccontextmanager
    async def __call__(self, chat_id: str, draft_id: int) -> AsyncIterator[DraftSink]:
        sink = DraftSink()
        task = asyncio.create_task(self._show(chat_id, draft_id, sink))
        try:
            yield sink
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _show(self, chat_id: str, draft_id: int, sink: DraftSink) -> None:
        shown: str | None = None
        shown_at = 0.0
        while True:
            view = draft_view(sink.text)
            now = self._monotonic()
            if view != shown or now - shown_at >= self._refresh:
                try:
                    await self._telegram.send_draft(
                        chat_id=chat_id, draft_id=draft_id, markdown=view
                    )
                except TelegramRateLimited as exc:
                    await self._sleep(exc.retry_after_seconds)
                    continue
                except TelegramError as exc:
                    logger.debug("Draft update failed: %s", exc.code)
                shown, shown_at = view, now
            await self._sleep(self._interval)
