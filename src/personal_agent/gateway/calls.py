"""Model calls through llm-gateway: stable call IDs, bounded retries, content-free records.

Call IDs are `pa:{call_key}:{purpose}:{generation}:a{attempt}`, where the call key is
the turn's random UUID: stable across restarts, never reused by another database.
Resubmitting an ID replays a finished call for free, so a restart rebuilds the same
request and sends the same ID. The attempt rises only when the earlier ID cannot be
used again: its outcome is unknown, its stored result expired, or the rebuilt request
differs. Calls are streamed, so the text so far can be shown while it is written.

After a restart the gateway may still be running the call the old process started;
it answers `call_in_progress` until that run ends, so the same ID is resubmitted
until it replays the result.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from psycopg_pool import AsyncConnectionPool

from personal_agent.clock import Clock
from personal_agent.gateway.api import (
    BudgetExhaustedError,
    CallResult,
    Completed,
    ExecutionUnknownError,
    GatewayError,
    RetryableError,
    StreamEvent,
    TextDelta,
)

# The first attempt and one more after an unusable call ID.
MAX_ATTEMPTS = 2
# Gateway codes after which this call ID can never produce an answer.
_REPLAY_UNUSABLE_CODES = frozenset({"idempotency_conflict", "call_result_expired"})
# The gateway ends a call within 180 s and marks it unknown once its 240 s lease
# lapses (checked every 15 s), so a running call resolves well within this.
_RUNNING_CALL_WAIT = timedelta(seconds=300)
_RUNNING_CALL_POLL_SECONDS = 5.0
logger = logging.getLogger(__name__)

CallStatus = Literal[
    "completed",
    "incomplete",
    "stopped",
    "execution_unknown",
    "replay_conflict",
    "budget_exhausted",
    "failed",
]


class Responses(Protocol):
    def stream(
        self,
        *,
        provider_call_id: str,
        operation: str,
        input: str | list[dict[str, Any]],
        profile: str | None = None,
        instructions: str | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
    ) -> AsyncIterator[StreamEvent]: ...


class StreamEndedError(GatewayError):
    """The stream finished without its final event."""


@dataclass(frozen=True, slots=True)
class CallRequest:
    turn_id: int
    call_key: str
    person_id: int
    purpose: str
    generation: int
    profile: str
    instructions: str
    input: list[dict[str, Any]]
    prompt_cache_key: str
    max_output_tokens: int
    # None lets the gateway profile decide; set only to compare efforts.
    reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class CallOutcome:
    status: Literal["answered", "budget_exhausted", "failed"]
    text: str | None
    # The model_call row of the last call made, if any.
    model_call_id: int | None
    error_code: str | None = None


def call_id(call_key: str, purpose: str, generation: int, attempt: int) -> str:
    return f"pa:{call_key}:{purpose}:{generation}:a{attempt}"


async def _unusable_attempts(pool: AsyncConnectionPool, request: CallRequest) -> int:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT count(*) FROM model_call
            WHERE turn_id = %s AND purpose = %s AND generation = %s
              AND status IN ('execution_unknown', 'replay_conflict')
            """,
            (request.turn_id, request.purpose, request.generation),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _record(
    pool: AsyncConnectionPool,
    request: CallRequest,
    *,
    attempt: int,
    status: CallStatus,
    latency_ms: int,
    now: datetime,
    result: CallResult | None = None,
    error_code: str | None = None,
) -> int:
    usage = result.usage if result else None
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            INSERT INTO model_call (
                person_id, turn_id, call_id, purpose, generation, attempt, status, error_code,
                model, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
                cost_usd, replayed, latency_ms, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (call_id) DO UPDATE SET
                status = EXCLUDED.status, error_code = EXCLUDED.error_code,
                model = EXCLUDED.model, input_tokens = EXCLUDED.input_tokens,
                cached_input_tokens = EXCLUDED.cached_input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                reasoning_tokens = EXCLUDED.reasoning_tokens, cost_usd = EXCLUDED.cost_usd,
                replayed = EXCLUDED.replayed, latency_ms = EXCLUDED.latency_ms
            -- A call stopped by a shutdown is the same call its replay finishes.
            WHERE model_call.status = 'stopped'
            RETURNING id
            """,
            (
                request.person_id,
                request.turn_id,
                call_id(request.call_key, request.purpose, request.generation, attempt),
                request.purpose,
                request.generation,
                attempt,
                status,
                error_code,
                result.model if result else None,
                usage.input_tokens if usage else None,
                usage.cached_input_tokens if usage else None,
                usage.output_tokens if usage else None,
                usage.reasoning_tokens if usage else None,
                Decimal(result.cost_usd) if result and result.cost_usd is not None else None,
                result.replayed if result else False,
                latency_ms,
                now,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            # Recorded before a restart; the replayed call is the same call.
            cursor = await connection.execute(
                "SELECT id FROM model_call WHERE call_id = %s",
                (call_id(request.call_key, request.purpose, request.generation, attempt),),
            )
            row = await cursor.fetchone()
    if row is None:
        raise LookupError("model_call row missing after insert")
    return int(row[0])


async def run_call(
    pool: AsyncConnectionPool,
    responses: Responses,
    request: CallRequest,
    *,
    clock: Clock,
    on_text: Callable[[str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CallOutcome:
    """Run one model call to an answer, a budget notice, or a failure.

    `on_text` receives the text written so far while the call streams. Cancelling
    the task records the call as stopped; the gateway still finishes and settles it.
    """
    attempt = 1 + await _unusable_attempts(pool, request)
    last_row: int | None = None
    while attempt <= MAX_ATTEMPTS:
        identifier = call_id(request.call_key, request.purpose, request.generation, attempt)
        started = time.monotonic()
        try:
            result = await _stream_after_running_call(
                responses, request, identifier, on_text, clock=clock, sleep=sleep
            )
        except asyncio.CancelledError:
            await _record(
                pool,
                request,
                attempt=attempt,
                status="stopped",
                latency_ms=_elapsed_ms(started),
                now=clock(),
                error_code="stopped",
            )
            logger.info("Call %s stopped", identifier)
            raise
        except ExecutionUnknownError:
            last_row = await _record(
                pool,
                request,
                attempt=attempt,
                status="execution_unknown",
                latency_ms=_elapsed_ms(started),
                now=clock(),
                error_code="execution_unknown",
            )
            logger.warning("Call %s outcome unknown; trying a new call ID", identifier)
            attempt += 1
            continue
        except BudgetExhaustedError:
            last_row = await _record(
                pool,
                request,
                attempt=attempt,
                status="budget_exhausted",
                latency_ms=_elapsed_ms(started),
                now=clock(),
                error_code="budget_exhausted",
            )
            logger.warning("Call %s refused: monthly budget exhausted", identifier)
            return CallOutcome("budget_exhausted", None, last_row, "budget_exhausted")
        except GatewayError as exc:
            if exc.code in _REPLAY_UNUSABLE_CODES:
                last_row = await _record(
                    pool,
                    request,
                    attempt=attempt,
                    status="replay_conflict",
                    latency_ms=_elapsed_ms(started),
                    now=clock(),
                    error_code=exc.code,
                )
                logger.warning("Call %s cannot be replayed (%s); new call ID", identifier, exc.code)
                attempt += 1
                continue
            last_row = await _record(
                pool,
                request,
                attempt=attempt,
                status="failed",
                latency_ms=_elapsed_ms(started),
                now=clock(),
                error_code=exc.code,
            )
            logger.error("Call %s failed: %s", identifier, exc.code)
            return CallOutcome("failed", None, last_row, exc.code)
        last_row = await _record(
            pool,
            request,
            attempt=attempt,
            status=result.status,
            latency_ms=_elapsed_ms(started),
            now=clock(),
            result=result,
        )
        text = (result.output_text or result.refusal or "").strip()
        logger.info(
            "Call %s %s: %d input (%d cached), %d output tokens, replayed=%s",
            identifier,
            result.status,
            result.usage.input_tokens,
            result.usage.cached_input_tokens,
            result.usage.output_tokens,
            result.replayed,
        )
        if not text:
            return CallOutcome("failed", None, last_row, "empty_output")
        return CallOutcome("answered", text, last_row)
    return CallOutcome("failed", None, last_row, "attempts_exhausted")


async def _stream_after_running_call(
    responses: Responses,
    request: CallRequest,
    identifier: str,
    on_text: Callable[[str], None] | None,
    *,
    clock: Clock,
    sleep: Callable[[float], Awaitable[None]],
) -> CallResult:
    """Stream the call, first waiting out a run of the same ID still at the gateway."""
    waiting_since: datetime | None = None
    while True:
        try:
            return await _stream(responses, request, identifier, on_text)
        except RetryableError as exc:
            if exc.code != "call_in_progress":
                raise
            if waiting_since is None:
                waiting_since = clock()
                logger.info("Call %s still running at the gateway; waiting for it", identifier)
            elif clock() - waiting_since >= _RUNNING_CALL_WAIT:
                raise
        await sleep(_RUNNING_CALL_POLL_SECONDS)


async def _stream(
    responses: Responses,
    request: CallRequest,
    identifier: str,
    on_text: Callable[[str], None] | None,
) -> CallResult:
    written = ""
    async for event in responses.stream(
        provider_call_id=identifier,
        operation=request.purpose,
        input=request.input,
        profile=request.profile,
        instructions=request.instructions,
        max_output_tokens=request.max_output_tokens,
        reasoning_effort=request.reasoning_effort,
        prompt_cache_key=request.prompt_cache_key,
    ):
        if isinstance(event, TextDelta):
            written += event.delta
            if on_text is not None:
                on_text(written)
        elif isinstance(event, Completed):
            # After a resubmission the final text is the authoritative one.
            return event.result
    raise StreamEndedError("stream_ended")


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
