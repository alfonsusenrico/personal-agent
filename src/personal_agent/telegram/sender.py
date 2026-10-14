"""Send outbox rows to Telegram in order, exactly once except in one narrow case.

A row is marked `sending` before the request and `sent` after it. Telegram has no
idempotency key, so a stop between Telegram accepting a message and the `sent`
update sends that one row again after restart; a lost reply would be worse.

Rows are rich messages (Telegram Rich Markdown), plain messages, reactions, polls,
or the bot's own name. A rich message Telegram rejects is sent once as plain text instead; one
rejected for its effect is sent again without the effect.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from personal_agent.clock import Clock
from personal_agent.telegram.api import TelegramApi, TelegramError, TelegramRateLimited
from personal_agent.telegram.render import split_reply
from personal_agent.telegram.rich import EFFECTS, to_plain

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
                        SELECT id, turn_id, chunk_index, kind, external_chat_id, text, payload,
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
                message_id, fallback = await self._send(row)
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
                    UPDATE outbox SET status = 'sent', external_message_id = %s, sent_at = %s,
                        fallback_used = %s
                    WHERE id = %s
                    """,
                    (message_id, self._clock(), fallback, row["id"]),
                )
            if row["turn_id"] is None:
                logger.info("Outbox %d %s sent", row["id"], row["kind"])
                continue
            logger.info(
                "Turn %d %s item %d sent%s",
                row["turn_id"],
                row["kind"],
                row["chunk_index"],
                " as plain text" if fallback else "",
            )

    async def _send(self, row: dict[str, Any]) -> tuple[str | None, bool]:
        """Send one row; returns the Telegram message ID and whether plain text was used."""
        chat_id: str = row["external_chat_id"]
        payload: dict[str, Any] = row["payload"]
        if row["kind"] == "reaction":
            await self._telegram.set_reaction(
                chat_id=chat_id, message_id=payload["target_message_id"], emoji=payload["emoji"]
            )
            return None, False
        if row["kind"] == "bot_name":
            await self._telegram.set_my_name(name=row["text"])
            return None, False
        if row["kind"] == "poll":
            return await self._send_poll(row["id"], chat_id), False
        if row["kind"] == "plain":
            message_id = await self._telegram.send_message(
                chat_id=chat_id,
                text=row["text"],
                reply_to_message_id=row["reply_to_external_message_id"],
            )
            return message_id, False
        return await self._send_rich(row, chat_id, payload)

    async def _send_rich(
        self, row: dict[str, Any], chat_id: str, payload: dict[str, Any]
    ) -> tuple[str, bool]:
        effect = payload.get("effect")
        reply_to = row["reply_to_external_message_id"]
        try:
            return (
                await self._telegram.send_rich_message(
                    chat_id=chat_id,
                    markdown=row["text"],
                    reply_to_message_id=reply_to,
                    effect_id=EFFECTS.get(effect) if effect else None,
                ),
                False,
            )
        except TelegramError as exc:
            if exc.code == "effect_invalid":
                logger.warning(
                    "Outbox %d effect %s rejected; sending without it", row["id"], effect
                )
                return await self._send_rich(row, chat_id, {**payload, "effect": None})
            if exc.status != 400:
                raise
            logger.warning("Outbox %d rich message rejected; sending plain text", row["id"])
        first: str | None = None
        for index, chunk in enumerate(split_reply(to_plain(row["text"]))):
            message_id = await self._telegram.send_message(
                chat_id=chat_id, text=chunk, reply_to_message_id=reply_to if index == 0 else None
            )
            first = first or message_id
        if first is None:
            raise TelegramError("empty_fallback", status=400)
        return first, True

    async def _send_poll(self, outbox_id: int, chat_id: str) -> str:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT question, options, allows_multiple FROM reply_poll WHERE outbox_id = %s",
                (outbox_id,),
            )
            poll = await cursor.fetchone()
        if poll is None:
            raise TelegramError("poll_missing", status=400)
        question, options, allows_multiple = poll
        message_id, poll_id = await self._telegram.send_poll(
            chat_id=chat_id, question=question, options=options, allows_multiple=allows_multiple
        )
        async with self._pool.connection() as connection:
            await connection.execute(
                "UPDATE reply_poll SET external_poll_id = %s WHERE outbox_id = %s",
                (poll_id, outbox_id),
            )
        return message_id

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
