"""The profile store against the real database: drafts, changes, history, removal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from personal_agent.profile import store
from personal_agent.profile.fields import normalise

pytestmark = [pytest.mark.anyio, pytest.mark.db]
NOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)


async def _person(connection: AsyncConnection) -> int:
    cursor = await connection.execute(
        "INSERT INTO person (created_at) VALUES (%s) RETURNING id", (NOW,)
    )
    row = await cursor.fetchone()
    assert row is not None
    await connection.execute(
        "INSERT INTO identity (person_id, channel, external_id, created_at) "
        "VALUES (%s, 'telegram', '1001', %s)",
        (row[0], NOW),
    )
    conversation = await connection.execute(
        "INSERT INTO conversation (person_id, kind, created_at) VALUES (%s, 'main', %s) RETURNING id",
        (row[0], NOW),
    )
    conversation_row = await conversation.fetchone()
    assert conversation_row is not None
    await connection.execute(
        "INSERT INTO turn (person_id, conversation_id, status, opened_at) "
        "VALUES (%s, %s, 'collecting', %s)",
        (row[0], conversation_row[0], NOW),
    )
    return row[0]


async def _turn(connection: AsyncConnection, person_id: int) -> int:
    cursor = await connection.execute(
        "SELECT id FROM turn WHERE person_id = %s ORDER BY id LIMIT 1", (person_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def _values(connection: AsyncConnection, person_id: int) -> list[tuple[Any, ...]]:
    cursor = await connection.execute(
        "SELECT field, value, status FROM profile_entry WHERE person_id = %s ORDER BY id",
        (person_id,),
    )
    return await cursor.fetchall()


async def _save(
    connection: AsyncConnection, person_id: int, turn_id: int, field: str, value: str
) -> bool:
    return await store.save(
        connection, person_id, field, value, source_event_ids=[1], turn_id=turn_id, now=NOW
    )


async def _timezone(connection: AsyncConnection, person_id: int) -> Any:
    cursor = await connection.execute("SELECT timezone FROM person WHERE id = %s", (person_id,))
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


def test_values_are_normalised_and_checked() -> None:
    assert normalise("address", "  Sam \n ") == "Sam"
    assert normalise("timezone", "Asia/Tokyo") == "Asia/Tokyo"
    assert normalise("timezone", "Tokyo time") is None
    assert normalise("timezone", "Asia/Tokyo (this week)") == "Asia/Tokyo"
    assert normalise("timezone", "America/Argentina/Buenos_Aires") == (
        "America/Argentina/Buenos_Aires"
    )
    assert normalise("timezone", "Mars/Olympus_Mons") is None
    assert normalise("assistant_name", "x" * 65) is None
    assert normalise("work", "") is None
    assert normalise("favourite_colour", "blue") is None


async def test_a_new_value_keeps_the_old_one_as_history(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)

        assert await _save(connection, person_id, turn_id, "address", "Samuel")
        assert await _save(connection, person_id, turn_id, "address", "Sam")
        assert not await _save(connection, person_id, turn_id, "address", "Sam")

        assert await _values(connection, person_id) == [
            ("address", "Samuel", "superseded"),
            ("address", "Sam", "active"),
        ]


async def test_undo_restores_what_the_reply_replaced(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        first = await _turn(connection, person_id)
        await _save(connection, person_id, first, "timezone", "Asia/Jakarta")
        cursor = await connection.execute(
            "INSERT INTO turn (person_id, conversation_id, status, opened_at, closed_at) "
            "SELECT person_id, conversation_id, 'answered', opened_at, opened_at FROM turn "
            "WHERE id = %s RETURNING id",
            (first,),
        )
        row = await cursor.fetchone()
        assert row is not None
        second = row[0]
        await _save(connection, person_id, second, "timezone", "Asia/Tokyo")
        await _save(connection, person_id, second, "work", "Engineer")
        assert await _timezone(connection, person_id) == "Asia/Tokyo"

        restored = await store.undo_turn(connection, person_id, second)

        assert sorted(restored or []) == [("timezone", "Asia/Jakarta"), ("work", None)]
        assert await _values(connection, person_id) == [("timezone", "Asia/Jakarta", "active")]
        assert await _timezone(connection, person_id) == "Asia/Jakarta"
        assert await store.undo_turn(connection, person_id, second) is None


async def test_remove_deletes_every_version_and_records_only_the_field(
    pool: AsyncConnectionPool,
) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        for value in ("Asia/Jakarta", "Asia/Tokyo"):
            await _save(connection, person_id, turn_id, "timezone", value)
        (entry,) = await store.active(connection, person_id)

        assert await store.remove(connection, person_id, entry.id, NOW) == "timezone"
        assert await store.remove(connection, person_id, entry.id, NOW) is None
        assert await _values(connection, person_id) == []
        assert await _timezone(connection, person_id) is None
        cursor = await connection.execute("SELECT * FROM profile_removal")
        assert len(await cursor.fetchall()) == 1
        assert await store.removed_since(connection, person_id, NOW - timedelta(minutes=1)) == [
            "timezone"
        ]
        assert await store.removed_since(connection, person_id, NOW) == []


async def test_questions_and_declines_are_counted_per_topic(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        await store.record_ask(connection, person_id, "routine", NOW)
        await store.record_ask(connection, person_id, "routine", NOW + timedelta(minutes=5))
        await store.record_decline(connection, person_id, "work", NOW)

        topics = await store.topics(connection, person_id)

        assert topics["routine"] == store.Topic(2, NOW + timedelta(minutes=5), False)
        assert topics["work"].declined


async def test_start_asks_now_until_cleared(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)

        assert not await store.asking_now(connection, person_id)
        await store.set_asking_now(connection, person_id, NOW)
        assert await store.asking_now(connection, person_id)
        await store.set_asking_now(connection, person_id, None)
        assert not await store.asking_now(connection, person_id)


async def test_the_bot_name_is_queued_for_the_owner_chat(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        await store.queue_bot_name(connection, person_id, "Nara", NOW)
        cursor = await connection.execute(
            "SELECT kind, text, external_chat_id, turn_id, status FROM outbox"
        )
        assert await cursor.fetchall() == [("bot_name", "Nara", "1001", None, "pending")]
