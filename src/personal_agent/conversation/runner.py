"""The turn runner: one generation at a time, from a closed debounce to a committed answer."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg_pool import AsyncConnectionPool

from personal_agent.clock import Clock
from personal_agent.conversation import notices, store
from personal_agent.conversation.context import MAX_WINDOW_MESSAGES, build_reply_request
from personal_agent.gateway.calls import CallRequest, Responses, run_call
from personal_agent.telegram.render import split_reply

REPLY_PURPOSE = "reply"
REPLY_MAX_OUTPUT_TOKENS = 2_000
# Waiting for new messages is event-driven; this only bounds a missed wake-up.
_IDLE_WAIT_SECONDS = 60.0
_ERROR_WAIT_SECONDS = 5.0
logger = logging.getLogger(__name__)

Typing = Callable[[str], AbstractAsyncContextManager[None]]


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    reply_profile: str
    default_timezone: str
    debounce: timedelta
    window_budget_tokens: int
    offline_after: timedelta
    # None lets the gateway profile decide; evaluation runs override it.
    reasoning_effort: str | None = None


class TurnRunner:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        responses: Responses,
        config: RunnerConfig,
        *,
        clock: Clock,
        typing: Typing,
        on_answer: Callable[[], None],
    ) -> None:
        self._pool = pool
        self._responses = responses
        self._config = config
        self._clock = clock
        self._typing = typing
        self._on_answer = on_answer

    async def step(self) -> float | None:
        """Advance the next open turn by one generation.

        Returns 0 to step again at once, the seconds until a debounce ends, or None
        when no turn is open.
        """
        async with self._pool.connection() as connection:
            turn = await store.next_open_turn(connection)
        if turn is None:
            return None
        now = self._clock()
        async with self._pool.connection() as connection, connection.transaction():
            began = await store.begin_generation(
                connection, turn.id, now=now, debounce=self._config.debounce
            )
        if began is None:
            return 0.0
        if isinstance(began, datetime):
            return max(0.05, (began - now).total_seconds())
        plan = began
        async with self._pool.connection() as connection:
            turn_input = await store.load_turn_input(
                connection,
                plan,
                default_timezone=self._config.default_timezone,
                window_limit=MAX_WINDOW_MESSAGES,
            )
        built = build_reply_request(
            turn_input,
            window_budget_tokens=self._config.window_budget_tokens,
            offline_after=self._config.offline_after,
        )
        request = CallRequest(
            turn_id=plan.turn_id,
            call_key=plan.call_key,
            person_id=plan.person_id,
            purpose=REPLY_PURPOSE,
            generation=plan.generation,
            profile=self._config.reply_profile,
            instructions=built.instructions,
            input=built.input,
            prompt_cache_key=built.prompt_cache_key,
            max_output_tokens=REPLY_MAX_OUTPUT_TOKENS,
            reasoning_effort=self._config.reasoning_effort,
        )
        async with self._typing(turn_input.chat_id):
            outcome = await run_call(self._pool, self._responses, request, clock=self._clock)
        async with self._pool.connection() as connection, connection.transaction():
            if not await store.lock_open_turn(connection, plan):
                return 0.0
            if outcome.status == "answered" and await store.mark_superseded_if_stale(
                connection, plan, outcome.model_call_id
            ):
                logger.info(
                    "Turn %d: a message joined during generation %d; preparing again",
                    plan.turn_id,
                    plan.generation,
                )
                return 0.0
            if outcome.status == "answered" and outcome.text:
                reply_text, failure_code = outcome.text, None
            elif outcome.status == "budget_exhausted":
                reply_text, failure_code = notices.BUDGET_EXHAUSTED, "budget_exhausted"
            else:
                reply_text, failure_code = notices.APOLOGY, outcome.error_code or "failed"
            chunks = split_reply(reply_text)
            await store.commit_answer(
                connection,
                plan,
                reply_chunks=chunks,
                reply_text=reply_text,
                model_call_id=outcome.model_call_id if failure_code is None else None,
                failure_code=failure_code,
                person_tz=turn_input.person_tz,
                now=self._clock(),
            )
        if failure_code is None:
            logger.info(
                "Turn %d answered: generation %d, %d events, %d chunks",
                plan.turn_id,
                plan.generation,
                len(turn_input.events),
                len(chunks),
            )
        else:
            logger.warning("Turn %d failed: %s", plan.turn_id, failure_code)
        self._on_answer()
        return 0.0

    async def run_forever(self, wakeup: asyncio.Event) -> None:
        while True:
            wakeup.clear()
            try:
                delay = await self.step()
            except Exception as exc:
                # Content-free: the class only. The turn stays open and is retried.
                logger.error("Turn runner error: %s", type(exc).__name__)
                delay = _ERROR_WAIT_SECONDS
            if delay == 0:
                continue
            timeout = _IDLE_WAIT_SECONDS if delay is None else delay
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wakeup.wait(), timeout)
