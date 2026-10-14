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


def test_values_are_normalised_and_checked() -> None:
    assert normalise("address", "  Sam \n ") == "Sam"
    assert normalise("timezone", "Asia/Tokyo") == "Asia/Tokyo"
    assert normalise("timezone", "Tokyo time") is None
    assert normalise("assistant_name", "x" * 65) is None
    assert normalise("work", "") is None
    assert normalise("favourite_colour", "blue") is None


async def test_introduction_drafts_count_only_after_save(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        await store.start_introduction(connection, person_id, NOW)
        await store.save_draft(
            connection, person_id, "address", "Sam", source_event_ids=[1], turn_id=turn_id, now=NOW
        )
        await store.save_draft(
            connection,
            person_id,
            "timezone",
            "Asia/Tokyo",
            source_event_ids=[1],
            turn_id=turn_id,
            now=NOW,
        )
        await store.skip(connection, person_id, "interests", NOW)

        assert await store.entries(connection, person_id, "active") == []
        intro = await store.introduction(connection, person_id)
        assert intro == store.Introduction("active", ("interests",))

        changed = await store.save_introduction(connection, person_id, NOW)

        assert changed is not None and [entry.field for entry in changed] == ["address", "timezone"]
        assert await _values(connection, person_id) == [
            ("address", "Sam", "active"),
            ("timezone", "Asia/Tokyo", "active"),
        ]
        cursor = await connection.execute("SELECT timezone FROM person WHERE id = %s", (person_id,))
        assert await cursor.fetchone() == ("Asia/Tokyo",)
        assert await store.introduction(connection, person_id) == store.Introduction(
            "completed", ()
        )
        assert await store.save_introduction(connection, person_id, NOW) is None


async def test_a_confirmed_change_keeps_the_old_value_as_history(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        for value in ("Samuel", "Sam"):
            await store.propose(
                connection,
                person_id,
                "address",
                value,
                source_event_ids=[1],
                turn_id=turn_id,
                now=NOW,
            )
            assert await store.confirm_pending(connection, person_id, turn_id, NOW) is not None

        assert await _values(connection, person_id) == [
            ("address", "Samuel", "superseded"),
            ("address", "Sam", "active"),
        ]


async def test_a_newer_proposal_replaces_an_unconfirmed_one(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        await store.propose(
            connection, person_id, "address", "Sam", source_event_ids=[1], turn_id=turn_id, now=NOW
        )
        await store.propose(
            connection,
            person_id,
            "address",
            "Sammy",
            source_event_ids=[2],
            turn_id=turn_id,
            now=NOW,
        )

        assert await _values(connection, person_id) == [("address", "Sammy", "pending")]
        assert await store.cancel_pending(connection, person_id, turn_id)
        assert await store.confirm_pending(connection, person_id, turn_id, NOW) is None
        assert await _values(connection, person_id) == []


async def test_confirming_the_current_value_adds_no_history(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        for _ in range(2):
            await store.propose(
                connection,
                person_id,
                "address",
                "Sam",
                source_event_ids=[1],
                turn_id=turn_id,
                now=NOW,
            )
            await store.confirm_pending(connection, person_id, turn_id, NOW)

        assert await _values(connection, person_id) == [("address", "Sam", "active")]


async def test_remove_deletes_every_version_and_records_only_the_field(
    pool: AsyncConnectionPool,
) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        for value in ("Asia/Jakarta", "Asia/Tokyo"):
            await store.propose(
                connection,
                person_id,
                "timezone",
                value,
                source_event_ids=[1],
                turn_id=turn_id,
                now=NOW,
            )
            await store.confirm_pending(connection, person_id, turn_id, NOW)
        (active,) = await store.entries(connection, person_id, "active")

        assert await store.remove(connection, person_id, active.id, NOW) == "timezone"
        assert await store.remove(connection, person_id, active.id, NOW) is None
        assert await _values(connection, person_id) == []
        cursor = await connection.execute("SELECT timezone FROM person WHERE id = %s", (person_id,))
        assert await cursor.fetchone() == (None,)
        cursor = await connection.execute("SELECT * FROM profile_removal")
        assert len(await cursor.fetchall()) == 1
        assert await store.removed_since(connection, person_id, NOW - timedelta(minutes=1)) == [
            "timezone"
        ]
        assert await store.removed_since(connection, person_id, NOW) == []


async def test_restarting_a_completed_introduction_starts_from_the_profile(
    pool: AsyncConnectionPool,
) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        turn_id = await _turn(connection, person_id)
        await store.start_introduction(connection, person_id, NOW)
        await store.save_draft(
            connection, person_id, "address", "Sam", source_event_ids=[1], turn_id=turn_id, now=NOW
        )
        await store.skip(connection, person_id, "work", NOW)
        await store.save_introduction(connection, person_id, NOW)

        await store.start_introduction(connection, person_id, NOW)

        drafts = await store.entries(connection, person_id, "draft")
        assert [(entry.field, entry.value) for entry in drafts] == [("address", "Sam")]
        # A review: topics without a value stay skipped, so nothing is asked again.
        intro = await store.introduction(connection, person_id)
        assert intro is not None and intro.state == "active"
        assert "address" not in intro.skipped and "work" in intro.skipped
        assert len(intro.skipped) == 7
        assert await store.save_introduction(connection, person_id, NOW) == []
        assert await _values(connection, person_id) == [("address", "Sam", "active")]


async def test_the_offer_is_recorded_once_and_later_declines_it(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)

        assert await store.mark_offered(connection, person_id, NOW)
        assert not await store.mark_offered(connection, person_id, NOW)
        assert await store.decline_introduction(connection, person_id, NOW)
        assert not await store.decline_introduction(connection, person_id, NOW)
        assert await store.introduction(connection, person_id) == store.Introduction("declined", ())


async def test_the_bot_name_is_queued_for_the_owner_chat(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as connection:
        person_id = await _person(connection)
        await store.queue_bot_name(connection, person_id, "Nara", NOW)
        cursor = await connection.execute(
            "SELECT kind, text, external_chat_id, turn_id, status FROM outbox"
        )
        assert await cursor.fetchall() == [("bot_name", "Nara", "1001", None, "pending")]
