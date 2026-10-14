"""SQL for the owner profile and the pacing of getting-to-know questions.

Every function expects the caller's transaction. A saved value supersedes the
active one and records it, so Undo can restore it; `remove` deletes every version
of a field and records only that it happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from personal_agent.profile.fields import FIELDS

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    field: str
    value: str
    saved_at: datetime
    turn_id: int | None


@dataclass(frozen=True, slots=True)
class Topic:
    asks: int
    last_asked_at: datetime | None
    declined: bool


async def _rows(
    connection: AsyncConnection, query: LiteralString, params: tuple[Any, ...]
) -> list[dict[str, Any]]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)
        return await cursor.fetchall()


async def active(connection: AsyncConnection, person_id: int) -> list[Entry]:
    """The person's current values, in field order."""
    rows = await _rows(
        connection,
        """
        SELECT id, field, value, saved_at, turn_id FROM profile_entry
        WHERE person_id = %s AND status = 'active'
        """,
        (person_id,),
    )
    entries = [
        Entry(row["id"], row["field"], row["value"], row["saved_at"], row["turn_id"])
        for row in rows
    ]
    return sorted(entries, key=lambda entry: FIELDS.index(entry.field))


async def _set_timezone(connection: AsyncConnection, person_id: int, value: str | None) -> None:
    await connection.execute("UPDATE person SET timezone = %s WHERE id = %s", (value, person_id))


async def save(
    connection: AsyncConnection,
    person_id: int,
    field: str,
    value: str,
    *,
    source_event_ids: list[int],
    turn_id: int,
    now: datetime,
) -> bool:
    """Make `value` the field's active value. False when it already is."""
    current = await _rows(
        connection,
        """
        SELECT id, value FROM profile_entry
        WHERE person_id = %s AND field = %s AND status = 'active'
        FOR UPDATE
        """,
        (person_id, field),
    )
    if current and current[0]["value"] == value:
        return False
    replaces = current[0]["id"] if current else None
    if replaces is not None:
        await connection.execute(
            "UPDATE profile_entry SET status = 'superseded', superseded_at = %s WHERE id = %s",
            (now, replaces),
        )
    await connection.execute(
        """
        INSERT INTO profile_entry (person_id, field, value, status, source_event_ids, turn_id,
            replaces_id, saved_at)
        VALUES (%s, %s, %s, 'active', %s, %s, %s, %s)
        """,
        (person_id, field, value, source_event_ids, turn_id, replaces, now),
    )
    if field == "timezone":
        await _set_timezone(connection, person_id, value)
    logger.info("Profile %s saved for person %d", field, person_id)
    return True


async def undo_turn(
    connection: AsyncConnection, person_id: int, turn_id: int
) -> list[tuple[str, str | None]] | None:
    """Revert the values a reply saved to what they replaced. None when none of them is
    still active; otherwise each field with its restored value (None for empty)."""
    saved = await _rows(
        connection,
        """
        SELECT id, field, replaces_id FROM profile_entry
        WHERE person_id = %s AND turn_id = %s AND status = 'active'
        FOR UPDATE
        """,
        (person_id, turn_id),
    )
    if not saved:
        return None
    restored: list[tuple[str, str | None]] = []
    for row in saved:
        await connection.execute("DELETE FROM profile_entry WHERE id = %s", (row["id"],))
        value = None
        if row["replaces_id"] is not None:
            back = await _rows(
                connection,
                """
                UPDATE profile_entry SET status = 'active', superseded_at = NULL
                WHERE id = %s RETURNING value
                """,
                (row["replaces_id"],),
            )
            value = back[0]["value"] if back else None
        if row["field"] == "timezone":
            await _set_timezone(connection, person_id, value)
        restored.append((row["field"], value))
        logger.info("Profile %s undone for person %d", row["field"], person_id)
    return restored


async def remove(
    connection: AsyncConnection, person_id: int, entry_id: int, now: datetime
) -> str | None:
    """Delete every version of the entry's field. None when the entry is no longer active."""
    rows = await _rows(
        connection,
        "SELECT field FROM profile_entry WHERE id = %s AND person_id = %s AND status = 'active'",
        (entry_id, person_id),
    )
    if not rows:
        return None
    field: str = rows[0]["field"]
    await connection.execute(
        "DELETE FROM profile_entry WHERE person_id = %s AND field = %s", (person_id, field)
    )
    await connection.execute(
        "INSERT INTO profile_removal (person_id, field, removed_at) VALUES (%s, %s, %s)",
        (person_id, field, now),
    )
    if field == "timezone":
        await _set_timezone(connection, person_id, None)
    logger.info("Profile %s removed for person %d", field, person_id)
    return field


async def removed_since(connection: AsyncConnection, person_id: int, since: datetime) -> list[str]:
    """Fields removed after `since`, in field order."""
    rows = await _rows(
        connection,
        "SELECT DISTINCT field FROM profile_removal WHERE person_id = %s AND removed_at > %s",
        (person_id, since),
    )
    removed = {row["field"] for row in rows}
    return [field for field in FIELDS if field in removed]


async def topics(connection: AsyncConnection, person_id: int) -> dict[str, Topic]:
    rows = await _rows(
        connection,
        "SELECT field, asks, last_asked_at, declined_at FROM profile_topic WHERE person_id = %s",
        (person_id,),
    )
    return {
        row["field"]: Topic(row["asks"], row["last_asked_at"], row["declined_at"] is not None)
        for row in rows
    }


async def record_ask(
    connection: AsyncConnection, person_id: int, field: str, now: datetime
) -> None:
    await connection.execute(
        """
        INSERT INTO profile_topic (person_id, field, asks, last_asked_at) VALUES (%s, %s, 1, %s)
        ON CONFLICT (person_id, field) DO UPDATE
        SET asks = profile_topic.asks + 1, last_asked_at = EXCLUDED.last_asked_at
        """,
        (person_id, field, now),
    )


async def record_decline(
    connection: AsyncConnection, person_id: int, field: str, now: datetime
) -> None:
    await connection.execute(
        """
        INSERT INTO profile_topic (person_id, field, declined_at) VALUES (%s, %s, %s)
        ON CONFLICT (person_id, field) DO UPDATE SET declined_at = EXCLUDED.declined_at
        """,
        (person_id, field, now),
    )
    logger.info("Profile topic %s declined by person %d", field, person_id)


async def turns_since(connection: AsyncConnection, person_id: int, since: datetime) -> int:
    """Owner turns (not commands) opened after `since`."""
    rows = await _rows(
        connection,
        """
        SELECT count(*) AS turns FROM turn
        WHERE person_id = %s AND opened_at > %s AND command IS NULL
        """,
        (person_id, since),
    )
    return int(rows[0]["turns"])


async def asking_now(connection: AsyncConnection, person_id: int) -> bool:
    rows = await _rows(
        connection,
        "SELECT asking_now_since FROM profile_pacing WHERE person_id = %s",
        (person_id,),
    )
    return bool(rows) and rows[0]["asking_now_since"] is not None


async def set_asking_now(
    connection: AsyncConnection, person_id: int, since: datetime | None
) -> None:
    """/start sets it; a reply in that mode that asks nothing clears it."""
    await connection.execute(
        """
        INSERT INTO profile_pacing (person_id, asking_now_since) VALUES (%s, %s)
        ON CONFLICT (person_id) DO UPDATE SET asking_now_since = EXCLUDED.asking_now_since
        """,
        (person_id, since),
    )


async def queue_bot_name(
    connection: AsyncConnection, person_id: int, name: str, now: datetime
) -> None:
    """Rename the bot through the outbox, so it is retried and sent once."""
    await connection.execute(
        """
        INSERT INTO outbox (person_id, channel, external_chat_id, chunk_index, kind, text,
            status, created_at)
        SELECT %s, i.channel, i.external_id, 0, 'bot_name', %s, 'pending', %s
        FROM identity i WHERE i.person_id = %s AND i.channel = 'telegram'
        ORDER BY i.id LIMIT 1
        """,
        (person_id, name, now, person_id),
    )
