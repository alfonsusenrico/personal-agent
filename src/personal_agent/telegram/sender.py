"""Send outbox rows to Telegram in order, exactly once except in one narrow case.

A row is marked `sending` before the request and `sent` after it. Telegram has no
idempotency key, so a stop between Telegram accepting a message and the `sent`
update sends that one chunk again after restart; a lost reply would be worse.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from personal_agent.clock import Clock
from personal_agent.telegram.api import TelegramApi, TelegramError, TelegramRateLimited

_IDLE_WAIT_SECONDS = 30.0
_MAX_BACKOFF_SECONDS = 60.0
logger = logging.getLogger(__name__)


class Sender:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        telegram: TelegramApi,
        *,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._telegram = telegram
        self._clock = clock
        self._sleep = sleep

    async def send_pending(self) -> bool:
        """Send every unsent row in order. False when a temporary error stopped it."""
        while True:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        """
                        SELECT id, turn_id, chunk_index, external_chat_id, text,
                               reply_to_external_message_id
                        FROM outbox WHERE status IN ('pending', 'sending')
                        ORDER BY id LIMIT 1
                        """
                    )
                    row = await cursor.fetchone()
                if row is None:
                    return True
                await connection.execute(
                    "UPDATE outbox SET status = 'sending', attempts = attempts + 1 WHERE id = %s",
                    (row["id"],),
                )
            try:
                message_id = await self._telegram.send_message(
                    chat_id=row["external_chat_id"],
                    text=row["text"],
                    reply_to_message_id=row["reply_to_external_message_id"],
                )
            except TelegramRateLimited as exc:
                logger.warning(
                    "Outbox %d rate limited; waiting %.0f s", row["id"], exc.retry_after_seconds
                )
                await self._sleep(exc.retry_after_seconds)
                continue
            except TelegramError as exc:
                if not exc.permanent:
                    logger.warning("Outbox %d not sent: %s; will retry", row["id"], exc.code)
                    return False
                async with self._pool.connection() as connection:
                    await connection.execute(
                        "UPDATE outbox SET status = 'failed', error_code = %s WHERE id = %s",
                        (exc.code, row["id"]),
                    )
                logger.error("Outbox %d rejected by Telegram: %s", row["id"], exc.code)
                continue
            async with self._pool.connection() as connection:
                await connection.execute(
                    """
                    UPDATE outbox SET status = 'sent', external_message_id = %s, sent_at = %s
                    WHERE id = %s
                    """,
                    (message_id, self._clock(), row["id"]),
                )
            logger.info("Turn %d chunk %d sent", row["turn_id"], row["chunk_index"])

    async def run_forever(self, wakeup: asyncio.Event) -> None:
        backoff = 1.0
        while True:
            wakeup.clear()
            try:
                finished = await self.send_pending()
            except Exception as exc:
                logger.error("Sender error: %s", type(exc).__name__)
                finished = False
            if finished:
                backoff = 1.0
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wakeup.wait(), _IDLE_WAIT_SECONDS)
            else:
                await self._sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
