"""SQL for the conversation core: ingest, turn state, answers, and the outbox.

Functions that change several rows expect the caller's transaction, so a crash
leaves either all of a step or none of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, LiteralString

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from personal_agent.conversation.context import render_turn
from personal_agent.conversation.models import EventRow, GenerationPlan, MessageRow, TurnInput
from personal_agent.telegram.updates import Envelope

CHANNEL = "telegram"
# One regeneration after a mid-turn message; later messages start the next turn.
MAX_GENERATIONS = 2


@dataclass(frozen=True, slots=True)
class IngestResult:
    event_id: int | None
    turn_id: int | None
    duplicate: bool


async def _one(
    connection: AsyncConnection, query: LiteralString, params: tuple[Any, ...]
) -> dict[str, Any] | None:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)
        return await cursor.fetchone()


async def _required(
    connection: AsyncConnection, query: LiteralString, params: tuple[Any, ...]
) -> dict[str, Any]:
    row = await _one(connection, query, params)
    if row is None:
        raise LookupError("expected a row")
    return row


async def _all(
    connection: AsyncConnection, query: LiteralString, params: tuple[Any, ...]
) -> list[dict[str, Any]]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)
        return await cursor.fetchall()


async def _person_for(
    connection: AsyncConnection, user_id: int, now: datetime
) -> tuple[int, int, str | None]:
    """The person, main conversation, and timezone for a Telegram user, created on first contact."""
    row = await _one(
        connection,
        """
        SELECT p.id AS person_id, c.id AS conversation_id, p.timezone
        FROM identity i
        JOIN person p ON p.id = i.person_id
        JOIN conversation c ON c.person_id = p.id AND c.kind = 'main'
        WHERE i.channel = %s AND i.external_id = %s
        """,
        (CHANNEL, str(user_id)),
    )
    if row is not None:
        return row["person_id"], row["conversation_id"], row["timezone"]
    person = await _required(
        connection, "INSERT INTO person (created_at) VALUES (%s) RETURNING id", (now,)
    )
    await connection.execute(
        "INSERT INTO identity (person_id, channel, external_id, created_at) VALUES (%s, %s, %s, %s)",
        (person["id"], CHANNEL, str(user_id), now),
    )
    conversation = await _required(
        connection,
        "INSERT INTO conversation (person_id, kind, created_at) VALUES (%s, 'main', %s) RETURNING id",
        (person["id"], now),
    )
    return person["id"], conversation["id"], None


async def _turn_for_new_event(
    connection: AsyncConnection, person_id: int, conversation_id: int, now: datetime
) -> int:
    """The open turn a new event joins, or a new one.

    A generating turn still takes the event while only its first generation has
    started; the runner then prepares the reply again with it.
    """
    rows = await _all(
        connection,
        """
        SELECT id, status, generation FROM turn
        WHERE conversation_id = %s AND status IN ('collecting', 'generating')
        ORDER BY id
        FOR UPDATE
        """,
        (conversation_id,),
    )
    for row in rows:
        if row["status"] == "collecting":
            return row["id"]
    for row in rows:
        if row["status"] == "generating" and row["generation"] < MAX_GENERATIONS:
            return row["id"]
    created = await _required(
        connection,
        """
        INSERT INTO turn (person_id, conversation_id, status, opened_at)
        VALUES (%s, %s, 'collecting', %s) RETURNING id
        """,
        (person_id, conversation_id, now),
    )
    return created["id"]


async def ingest(
    connection: AsyncConnection, envelope: Envelope, *, now: datetime, default_timezone: str
) -> IngestResult:
    """Store an envelope once and attach it to a turn. Call inside a transaction."""
    person_id, conversation_id, timezone = await _person_for(connection, envelope.user_id, now)
    existing = await _one(
        connection,
        "SELECT id FROM inbound_event WHERE channel = %s AND external_update_id = %s",
        (CHANNEL, str(envelope.update_id)),
    )
    if existing is not None:
        return IngestResult(event_id=existing["id"], turn_id=None, duplicate=True)
    edit_of = None
    if envelope.kind == "edit":
        original = await _one(
            connection,
            """
            SELECT id FROM inbound_event
            WHERE channel = %s AND external_chat_id = %s AND external_message_id = %s
              AND kind <> 'edit'
            ORDER BY id LIMIT 1
            """,
            (CHANNEL, str(envelope.chat_id), str(envelope.message_id)),
        )
        edit_of = original["id"] if original else None
    turn_id = None
    if envelope.answerable:
        turn_id = await _turn_for_new_event(connection, person_id, conversation_id, now)
    row = await _required(
        connection,
        """
        INSERT INTO inbound_event (
            person_id, conversation_id, turn_id, channel, external_update_id,
            external_chat_id, external_message_id, kind, modality, occurred_at,
            received_at, person_tz, trust, forward_origin, reply_to_external_message_id,
            edit_of, text, raw
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            person_id,
            conversation_id,
            turn_id,
            CHANNEL,
            str(envelope.update_id),
            str(envelope.chat_id),
            str(envelope.message_id),
            envelope.kind,
            envelope.modality,
            envelope.occurred_at,
            now,
            timezone or default_timezone,
            envelope.trust,
            Jsonb(envelope.forward_origin) if envelope.forward_origin else None,
            str(envelope.reply_to_message_id) if envelope.reply_to_message_id else None,
            edit_of,
            envelope.text,
            Jsonb(envelope.raw),
        ),
    )
    return IngestResult(event_id=row["id"], turn_id=turn_id, duplicate=False)


@dataclass(frozen=True, slots=True)
class OpenTurn:
    id: int
    status: Literal["collecting", "generating"]


async def next_open_turn(connection: AsyncConnection) -> OpenTurn | None:
    """The turn to work on next: a generating turn finishes before a collecting one starts."""
    row = await _one(
        connection,
        """
        SELECT id, status FROM turn
        WHERE status IN ('collecting', 'generating')
        ORDER BY (status = 'generating') DESC, id
        LIMIT 1
        """,
        (),
    )
    return OpenTurn(row["id"], row["status"]) if row else None


async def _latest_event(connection: AsyncConnection, turn_id: int) -> tuple[int, datetime]:
    row = await _required(
        connection,
        "SELECT max(id) AS id, max(received_at) AS at FROM inbound_event WHERE turn_id = %s",
        (turn_id,),
    )
    if row["id"] is None:
        raise LookupError("an open turn has no events")
    return row["id"], row["at"]


async def begin_generation(
    connection: AsyncConnection, turn_id: int, *, now: datetime, debounce: timedelta
) -> GenerationPlan | datetime | None:
    """Start, restart, or resume a turn's generation. Call inside a transaction.

    Returns the plan, the time the turn becomes ready (still within the debounce),
    or None when the turn is no longer open.
    """
    turn = await _one(
        connection,
        """
        SELECT id, call_key, person_id, conversation_id, status, generation, generation_started_at,
               generated_through_event_id
        FROM turn WHERE id = %s FOR UPDATE
        """,
        (turn_id,),
    )
    if turn is None or turn["status"] not in ("collecting", "generating"):
        return None
    last_event_id, last_event_at = await _latest_event(connection, turn_id)
    ready_at = last_event_at + debounce
    needs_new_generation = turn["status"] == "collecting" or (
        last_event_id > turn["generated_through_event_id"] and turn["generation"] < MAX_GENERATIONS
    )
    if needs_new_generation:
        if now < ready_at:
            return ready_at
        generation = turn["generation"] + 1
        await connection.execute(
            """
            UPDATE turn SET status = 'generating', generation = %s, generation_started_at = %s,
                generated_through_event_id = %s
            WHERE id = %s
            """,
            (generation, now, last_event_id, turn_id),
        )
        started_at, through = now, last_event_id
    else:
        generation = turn["generation"]
        started_at, through = turn["generation_started_at"], turn["generated_through_event_id"]
    return GenerationPlan(
        turn_id=turn_id,
        call_key=str(turn["call_key"]),
        person_id=turn["person_id"],
        conversation_id=turn["conversation_id"],
        generation=generation,
        started_at=started_at,
        through_event_id=through,
    )


async def load_turn_events(
    connection: AsyncConnection, turn_id: int, *, through_event_id: int | None = None
) -> list[EventRow]:
    rows = await _all(
        connection,
        """
        SELECT e.id, e.kind, e.modality, e.occurred_at, e.trust, e.forward_origin, e.text,
               e.external_chat_id, e.external_message_id, e.edit_of,
               original.turn_id = e.turn_id AS edits_same_turn,
               (
                   SELECT prior.text FROM inbound_event prior
                   WHERE (prior.id = e.edit_of OR prior.edit_of = e.edit_of) AND prior.id < e.id
                   ORDER BY prior.id DESC LIMIT 1
               ) AS previous_text
        FROM inbound_event e
        LEFT JOIN inbound_event original ON original.id = e.edit_of
        WHERE e.turn_id = %s AND (%s::bigint IS NULL OR e.id <= %s::bigint)
        ORDER BY e.id
        """,
        (turn_id, through_event_id, through_event_id),
    )
    return [
        EventRow(
            id=row["id"],
            kind=row["kind"],
            modality=row["modality"],
            occurred_at=row["occurred_at"],
            trust=row["trust"],
            forward_origin=row["forward_origin"],
            text=row["text"],
            external_chat_id=row["external_chat_id"],
            external_message_id=row["external_message_id"],
            edit_of=row["edit_of"],
            edits_same_turn=bool(row["edits_same_turn"]),
            previous_text=row["previous_text"],
        )
        for row in rows
    ]


async def load_turn_input(
    connection: AsyncConnection, plan: GenerationPlan, *, default_timezone: str, window_limit: int
) -> TurnInput:
    events = await load_turn_events(
        connection, plan.turn_id, through_event_id=plan.through_event_id
    )
    person = await _required(
        connection, "SELECT timezone FROM person WHERE id = %s", (plan.person_id,)
    )
    # Earlier turns are all closed: one turn per conversation generates at a time,
    # in order, so this window is the same on every rebuild of this generation.
    rows = await _all(
        connection,
        """
        SELECT id, role, text, occurred_at, person_tz FROM message
        WHERE conversation_id = %s AND turn_id < %s
        ORDER BY id DESC LIMIT %s
        """,
        (plan.conversation_id, plan.turn_id, window_limit),
    )
    window = [
        MessageRow(row["id"], row["role"], row["text"], row["occurred_at"], row["person_tz"])
        for row in reversed(rows)
    ]
    return TurnInput(
        plan=plan,
        person_tz=person["timezone"] or default_timezone,
        chat_id=events[-1].external_chat_id,
        events=events,
        window=window,
    )


async def mark_superseded_if_stale(
    connection: AsyncConnection, plan: GenerationPlan, model_call_id: int | None
) -> bool:
    """True when messages joined the turn after this generation started and one more
    generation is allowed. Call inside the answer's transaction, after locking the turn."""
    last_event_id, _ = await _latest_event(connection, plan.turn_id)
    if last_event_id <= plan.through_event_id or plan.generation >= MAX_GENERATIONS:
        return False
    if model_call_id is not None:
        await connection.execute(
            "UPDATE model_call SET status = 'superseded' WHERE id = %s", (model_call_id,)
        )
    return True


async def lock_open_turn(connection: AsyncConnection, plan: GenerationPlan) -> bool:
    row = await _one(
        connection,
        "SELECT status, generation FROM turn WHERE id = %s FOR UPDATE",
        (plan.turn_id,),
    )
    return (
        row is not None and row["status"] == "generating" and row["generation"] == plan.generation
    )


async def _reply_reference(
    connection: AsyncConnection, plan: GenerationPlan, events: list[EventRow]
) -> str | None:
    """Reply to a message only when it is not the latest thing the owner sent."""
    last = events[-1]
    newer = await _one(
        connection,
        "SELECT 1 AS found FROM inbound_event WHERE conversation_id = %s AND id > %s LIMIT 1",
        (plan.conversation_id, last.id),
    )
    only_earlier_edits = all(event.kind == "edit" and not event.edits_same_turn for event in events)
    if newer is not None or only_earlier_edits:
        return last.external_message_id
    return None


async def commit_answer(
    connection: AsyncConnection,
    plan: GenerationPlan,
    *,
    reply_chunks: list[str],
    reply_text: str,
    model_call_id: int | None,
    failure_code: str | None,
    person_tz: str,
    now: datetime,
) -> int:
    """Write the turn's user and assistant messages and the outbox, and close the turn.
    Call inside a transaction, after `lock_open_turn`. Returns the outbox row count."""
    events = await load_turn_events(connection, plan.turn_id)
    rendered = render_turn(events)
    chat_id = events[-1].external_chat_id
    await connection.execute(
        """
        INSERT INTO message (
            person_id, conversation_id, turn_id, role, text, occurred_at, person_tz, trust,
            source_event_ids, created_at
        ) VALUES (%s, %s, %s, 'user', %s, %s, %s, %s, %s, %s)
        """,
        (
            plan.person_id,
            plan.conversation_id,
            plan.turn_id,
            rendered.text,
            rendered.first_occurred_at,
            person_tz,
            rendered.trust,
            rendered.source_event_ids,
            now,
        ),
    )
    assistant = await _required(
        connection,
        """
        INSERT INTO message (
            person_id, conversation_id, turn_id, role, text, occurred_at, person_tz, trust,
            source_event_ids, model_call_id, created_at
        ) VALUES (%s, %s, %s, 'assistant', %s, %s, %s, 'assistant', %s, %s, %s)
        RETURNING id
        """,
        (
            plan.person_id,
            plan.conversation_id,
            plan.turn_id,
            reply_text,
            now,
            person_tz,
            rendered.source_event_ids,
            model_call_id,
            now,
        ),
    )
    reply_to = await _reply_reference(connection, plan, events)
    for index, chunk in enumerate(reply_chunks):
        await connection.execute(
            """
            INSERT INTO outbox (
                person_id, turn_id, message_id, channel, external_chat_id, chunk_index, text,
                reply_to_external_message_id, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            """,
            (
                plan.person_id,
                plan.turn_id,
                assistant["id"],
                CHANNEL,
                chat_id,
                index,
                chunk,
                reply_to if index == 0 else None,
                now,
            ),
        )
    await connection.execute(
        "UPDATE turn SET status = %s, failure_code = %s, closed_at = %s WHERE id = %s",
        ("failed" if failure_code else "answered", failure_code, now, plan.turn_id),
    )
    return len(reply_chunks)
