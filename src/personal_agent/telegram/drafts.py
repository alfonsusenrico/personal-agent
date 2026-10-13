"""Show a reply as a live Telegram draft while it is written.

Until the model writes visible text, Telegram's "typing…" status shows instead of
a draft: Telegram cannot withdraw a draft, so a reply that turns out to be only a
reaction would leave a placeholder draft behind for up to 30 seconds. From the
first text on, the draft shows the text so far with a Stop button, at most once
per interval and only when it changed. A draft lasts 30 seconds, so an unchanged
one is sent again before it fades. Both are cosmetic: a failed update is logged
by code and never delays the reply.
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
# Telegram shows the typing status for five seconds or less; refresh it sooner.
TYPING_REFRESH_SECONDS = 4.0
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
        typing_refresh_seconds: float = TYPING_REFRESH_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._telegram = telegram
        self._interval = interval_seconds
        self._refresh = refresh_seconds
        self._typing_refresh = typing_refresh_seconds
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
        shown = ""
        shown_at = 0.0
        typed_at: float | None = None
        while True:
            # Once text was shown it stays, even if the view is briefly empty again.
            view = draft_view(sink.text) or shown
            now = self._monotonic()
            if not view:
                typing_due = typed_at is None or now - typed_at >= self._typing_refresh
                if typing_due and await self._attempt(self._telegram.send_typing(chat_id=chat_id)):
                    typed_at = now
            elif view != shown or now - shown_at >= self._refresh:
                update = self._telegram.send_draft(
                    chat_id=chat_id, draft_id=draft_id, markdown=view
                )
                if await self._attempt(update):
                    shown, shown_at = view, now
            await self._sleep(self._interval)

    async def _attempt(self, request: Awaitable[None]) -> bool:
        """Send one cosmetic update; False when flood control asks to try again later."""
        try:
            await request
        except TelegramRateLimited as exc:
            await self._sleep(exc.retry_after_seconds)
            return False
        except TelegramError as exc:
            logger.debug("Draft update failed: %s", exc.code)
        return True
