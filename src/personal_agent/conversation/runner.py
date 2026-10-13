"""The turn runner: one generation at a time, from a closed debounce to a committed answer."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from psycopg_pool import AsyncConnectionPool

from personal_agent.clock import Clock
from personal_agent.conversation import notices, store
from personal_agent.conversation.context import (
    MAX_WINDOW_MESSAGES,
    build_reply_request,
    shared_urls,
)
from personal_agent.conversation.models import GenerationPlan, TurnInput
from personal_agent.conversation.store import OutgoingItem, TurnOutcome
from personal_agent.gateway.calls import CallOutcome, CallRequest, Responses, run_call
from personal_agent.telegram.render import split_reply
from personal_agent.telegram.rich import RenderedReply, render_reply

REPLY_PURPOSE = "reply"
REPLY_MAX_OUTPUT_TOKENS = 2_000
# Recorded so the next reply knows; written in brackets like other system notes.
STOPPED_RECORD = "[stopped by the owner before the reply was finished]"
# Waiting for new messages is event-driven; this only bounds a missed wake-up.
_IDLE_WAIT_SECONDS = 60.0
_ERROR_WAIT_SECONDS = 5.0
logger = logging.getLogger(__name__)


class Draft(Protocol):
    def update(self, text: str) -> None: ...


Drafts = Callable[[str, int], AbstractAsyncContextManager[Draft]]


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    reply_profile: str
    default_timezone: str
    debounce: timedelta
    window_budget_tokens: int
    offline_after: timedelta
    # None lets the gateway profile decide; evaluation runs override it.
    reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class _Commit:
    outcome: TurnOutcome
    assistant_text: str
    items: list[OutgoingItem]
    model_call_id: int | None = None
    failure_code: str | None = None


def _notice(text: str) -> list[OutgoingItem]:
    return [OutgoingItem("plain", chunk) for chunk in split_reply(text)]


def _items(rendered: RenderedReply) -> list[OutgoingItem]:
    """The reply in sending order: the reaction, the rich messages, then the poll."""
    items: list[OutgoingItem] = []
    if rendered.reaction:
        items.append(OutgoingItem("reaction", payload={"emoji": rendered.reaction}))
    for index, chunk in enumerate(rendered.chunks):
        payload = {"effect": rendered.effect} if rendered.effect and index == 0 else {}
        replies = [reply for reply in rendered.quick_replies if reply.token in chunk]
        items.append(OutgoingItem("rich", chunk, payload, quick_replies=replies))
    if rendered.poll is not None:
        items.append(OutgoingItem("poll", poll=rendered.poll))
    return items


class TurnRunner:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        responses: Responses,
        config: RunnerConfig,
        *,
        clock: Clock,
        drafts: Drafts,
        on_answer: Callable[[], None],
    ) -> None:
        self._pool = pool
        self._responses = responses
        self._config = config
        self._clock = clock
        self._drafts = drafts
        self._on_answer = on_answer
        self._active: tuple[int, asyncio.Task[CallOutcome]] | None = None
        self._stopping: int | None = None

    def request_stop(self, turn_id: int) -> None:
        """Cancel the turn's model call if it is running; the stored Stop covers the rest."""
        if self._active is not None and self._active[0] == turn_id:
            self._stopping = turn_id
            self._active[1].cancel()

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
        outcome = None if plan.stop_requested else await self._generate(plan, turn_input)
        async with self._pool.connection() as connection, connection.transaction():
            state = await store.lock_open_turn(connection, plan)
            if state == "closed":
                return 0.0
            if state == "stop" or outcome is None:
                commit = _Commit("stopped", STOPPED_RECORD, [])
            elif outcome.status == "answered" and await store.mark_superseded_if_stale(
                connection, plan, outcome.model_call_id
            ):
                logger.info(
                    "Turn %d: a message joined during generation %d; preparing again",
                    plan.turn_id,
                    plan.generation,
                )
                return 0.0
            else:
                commit = self._commit_for(outcome, turn_input)
            await store.commit_turn(
                connection,
                plan,
                outcome=commit.outcome,
                assistant_text=commit.assistant_text,
                items=commit.items,
                model_call_id=commit.model_call_id,
                failure_code=commit.failure_code,
                person_tz=turn_input.person_tz,
                now=self._clock(),
            )
        if commit.outcome == "answered":
            logger.info(
                "Turn %d answered: generation %d, %d events, %d items",
                plan.turn_id,
                plan.generation,
                len(turn_input.events),
                len(commit.items),
            )
        else:
            logger.warning("Turn %d %s: %s", plan.turn_id, commit.outcome, commit.failure_code)
        self._on_answer()
        return 0.0

    async def _generate(self, plan: GenerationPlan, turn_input: TurnInput) -> CallOutcome | None:
        """The call's outcome, or None when the owner stopped it."""
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
        async with self._drafts(turn_input.chat_id, plan.draft_id) as draft:
            task = asyncio.create_task(
                run_call(
                    self._pool, self._responses, request, clock=self._clock, on_text=draft.update
                )
            )
            self._active = (plan.turn_id, task)
            try:
                return await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if self._stopping != plan.turn_id or (current and current.cancelling()):
                    raise
                logger.info("Turn %d stopped by the owner", plan.turn_id)
                return None
            finally:
                self._active = None
                self._stopping = None

    def _commit_for(self, outcome: CallOutcome, turn_input: TurnInput) -> _Commit:
        if outcome.status == "budget_exhausted":
            return _Commit(
                "failed",
                notices.BUDGET_EXHAUSTED,
                _notice(notices.BUDGET_EXHAUSTED),
                failure_code="budget_exhausted",
            )
        if outcome.status == "answered" and outcome.text:
            owner_texts = [event.text or "" for event in turn_input.events]
            owner_texts += [message.text for message in turn_input.window if message.role == "user"]
            rendered = render_reply(outcome.text, shared_urls=shared_urls(owner_texts))
            if rendered.dropped:
                logger.info("Reply parts removed: %s", rendered.dropped)
            if rendered.sends_anything:
                return _Commit(
                    "answered",
                    rendered.stored,
                    _items(rendered),
                    model_call_id=outcome.model_call_id,
                )
            failure = "empty_output"
        else:
            failure = outcome.error_code or "failed"
        return _Commit("failed", notices.APOLOGY, _notice(notices.APOLOGY), failure_code=failure)

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
