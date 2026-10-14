"""Long-poll Telegram and store each update before confirming it.

The next getUpdates call confirms everything below its offset, and the offset only
moves past an update once it is stored (or deliberately ignored). A stop before
storing means Telegram delivers the update again; a stored duplicate is skipped.

After a button press is stored, the press is acknowledged and the message is redrawn
with the chosen option; a Stop press is passed to the runner. Both are cosmetic or
covered by stored state, so their failures are only logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import psycopg
from psycopg_pool import AsyncConnectionPool

from personal_agent.clock import Clock
from personal_agent.conversation.store import ButtonPress, ingest
from personal_agent.telegram.api import (
    TelegramApi,
    TelegramConflict,
    TelegramError,
    TelegramRateLimited,
)
from personal_agent.telegram.rich import mark_chosen
from personal_agent.telegram.updates import parse_update, update_id_of

LONG_POLL_SECONDS = 30
_CONFLICT_WAIT_SECONDS = 30.0
_PERMANENT_ERROR_WAIT_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 30.0
# Shown by Telegram when an old or used quick reply is pressed, in both of the
# owner's languages because no reply exists to mirror.
UNAVAILABLE_CHOICE = "No longer available · Sudah tidak berlaku"
logger = logging.getLogger(__name__)


class Poller:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        telegram: TelegramApi,
        *,
        allowed_user_ids: frozenset[int],
        default_timezone: str,
        clock: Clock,
        on_stored: Callable[[], None],
        on_stop: Callable[[int], None] = lambda _turn_id: None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        long_poll_seconds: int = LONG_POLL_SECONDS,
    ) -> None:
        self._pool = pool
        self._telegram = telegram
        self._allowed = allowed_user_ids
        self._default_timezone = default_timezone
        self._clock = clock
        self._on_stored = on_stored
        self._on_stop = on_stop
        self._sleep = sleep
        self._long_poll_seconds = long_poll_seconds
        self._offset: int | None = None

    async def poll_once(self) -> None:
        updates = await self._telegram.get_updates(
            offset=self._offset, timeout_seconds=self._long_poll_seconds
        )
        stored = duplicates = ignored = 0
        try:
            for update in updates:
                envelope = parse_update(update, self._allowed, now=self._clock())
                if envelope is None:
                    ignored += 1
                else:
                    async with self._pool.connection() as connection, connection.transaction():
                        result = await ingest(
                            connection,
                            envelope,
                            now=self._clock(),
                            default_timezone=self._default_timezone,
                        )
                    if result.duplicate:
                        duplicates += 1
                    else:
                        if result.button is not None:
                            await self._acknowledge(result.button)
                        if result.stopped_turn_id is not None:
                            self._on_stop(result.stopped_turn_id)
                        stored += 1
                        logger.info(
                            "Stored event %s (%s, %s) in turn %s",
                            result.event_id,
                            envelope.kind,
                            envelope.modality,
                            result.turn_id,
                        )
                update_id = update_id_of(update)
                if update_id is not None:
                    self._offset = max(self._offset or 0, update_id + 1)
        finally:
            # Wake the runner for what was stored, even if a later update failed.
            if stored:
                self._on_stored()
        if duplicates or ignored:
            logger.info("Skipped %d duplicate and ignored %d other updates", duplicates, ignored)

    async def _acknowledge(self, press: ButtonPress) -> None:
        try:
            await self._telegram.answer_callback(
                callback_query_id=press.callback_query_id,
                text=None if press.accepted else UNAVAILABLE_CHOICE,
            )
            if press.accepted and press.chat_id and press.message_id and press.markdown:
                await self._telegram.edit_rich_message(
                    chat_id=press.chat_id,
                    message_id=press.message_id,
                    markdown=mark_chosen(press.markdown, press.label or ""),
                )
        except TelegramError as exc:
            logger.warning("Button acknowledgement failed: %s", exc.code)

    async def run_forever(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self.poll_once()
                backoff = 1.0
                continue
            except TelegramConflict:
                logger.warning("Another getUpdates consumer or a webhook holds this bot")
                await self._sleep(_CONFLICT_WAIT_SECONDS)
                continue
            except TelegramRateLimited as exc:
                await self._sleep(exc.retry_after_seconds)
                continue
            except TelegramError as exc:
                logger.warning("Polling failed: %s", exc.code)
                if exc.permanent:
                    await self._sleep(_PERMANENT_ERROR_WAIT_SECONDS)
                    continue
            except psycopg.Error as exc:
                logger.error("Storing updates failed: %s", type(exc).__name__)
            await self._sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
