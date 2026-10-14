"""SQL for the owner profile and the introduction.

Every function expects the caller's transaction. A value becomes active only
through `confirm_pending` or `save_introduction`, which the owner's button press
triggers; a change keeps the old value as superseded history, and `remove`
deletes every version of a field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, LiteralString

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from personal_agent.profile.fields import FIELDS

logger = logging.getLogger(__name__)
IntroState = Literal["offered", "declined", "active", "completed"]


@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    field: str
    value: str
    status: str
    # When the value was confirmed; None for drafts and pending changes.
    confirmed_at: datetime | None
    turn_id: int | None


@dataclass(frozen=True, slots=True)
class Introduction:
    state: IntroState
    skipped: tuple[str, ...]


async def _rows(
    connection: AsyncConnection, query: LiteralString, params: tuple[Any, ...]
) -> list[dict[str, Any]]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)
        return await cursor.fetchall()


def _entry(row: dict[str, Any]) -> Entry:
    return Entry(
        id=row["id"],
        field=row["field"],
        value=row["value"],
        status=row["status"],
        confirmed_at=row["confirmed_at"],
        turn_id=row["turn_id"],
    )


def _in_field_order(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda entry: FIELDS.index(entry.field))


async def entries(connection: AsyncConnection, person_id: int, status: str) -> list[Entry]:
    """The person's entries with this status, in field order."""
    rows = await _rows(
        connection,
        """
        SELECT id, field, value, status, confirmed_at, turn_id FROM profile_entry
        WHERE person_id = %s AND status = %s
        """,
        (person_id, status),
    )
    return _in_field_order([_entry(row) for row in rows])


async def introduction(
    connection: AsyncConnection, person_id: int, *, lock: bool = False
) -> Introduction | None:
    query: LiteralString = (
        "SELECT state, skipped FROM introduction WHERE person_id = %s FOR UPDATE"
        if lock
        else "SELECT state, skipped FROM introduction WHERE person_id = %s"
    )
    rows = await _rows(connection, query, (person_id,))
    if not rows:
        return None
    return Introduction(rows[0]["state"], tuple(rows[0]["skipped"]))


async def removed_since(connection: AsyncConnection, person_id: int, since: datetime) -> list[str]:
    """Fields removed after `since`, in field order."""
    rows = await _rows(
        connection,
        "SELECT DISTINCT field FROM profile_removal WHERE person_id = %s AND removed_at > %s",
        (person_id, since),
    )
    return [field for field in FIELDS if field in {row["field"] for row in rows}]


async def mark_offered(connection: AsyncConnection, person_id: int, now: datetime) -> bool:
    """Record that the introduction was offered; False if it already has a state."""
    rows = await _rows(
        connection,
        """
        INSERT INTO introduction (person_id, state, offered_at, updated_at)
        VALUES (%s, 'offered', %s, %s)
        ON CONFLICT (person_id) DO NOTHING
        RETURNING person_id
        """,
        (person_id, now, now),
    )
    return bool(rows)


async def start_introduction(connection: AsyncConnection, person_id: int, now: datetime) -> None:
    """Begin or resume the introduction. After a completed one, the drafts start from the
    active profile, so the owner reviews it and changes only what they want."""
    current = await introduction(connection, person_id, lock=True)
    if current is not None and current.state == "active":
        return
    restarting = current is not None and current.state == "completed"
    await connection.execute(
        """
        INSERT INTO introduction (person_id, state, started_at, updated_at)
        VALUES (%s, 'active', %s, %s)
        ON CONFLICT (person_id) DO UPDATE SET state = 'active', started_at = EXCLUDED.started_at,
            completed_at = NULL, updated_at = EXCLUDED.updated_at,
            skipped = CASE WHEN introduction.state = 'completed' THEN '{}'
                           ELSE introduction.skipped END
        """,
        (person_id, now, now),
    )
    if restarting and not await entries(connection, person_id, "draft"):
        await connection.execute(
            """
            INSERT INTO profile_entry (person_id, field, value, status, source_event_ids,
                turn_id, created_at)
            SELECT person_id, field, value, 'draft', source_event_ids, turn_id, %s
            FROM profile_entry WHERE person_id = %s AND status = 'active'
            """,
            (now, person_id),
        )
    logger.info("Introduction started for person %d", person_id)


async def decline_introduction(connection: AsyncConnection, person_id: int, now: datetime) -> bool:
    """Later: the introduction is not offered again. False when it is no longer offered."""
    rows = await _rows(
        connection,
        """
        UPDATE introduction SET state = 'declined', updated_at = %s
        WHERE person_id = %s AND state = 'offered'
        RETURNING person_id
        """,
        (now, person_id),
    )
    return bool(rows)


async def save_draft(
    connection: AsyncConnection,
    person_id: int,
    field: str,
    value: str,
    *,
    source_event_ids: list[int],
    turn_id: int,
    now: datetime,
) -> None:
    await connection.execute(
        "DELETE FROM profile_entry WHERE person_id = %s AND field = %s AND status = 'draft'",
        (person_id, field),
    )
    await connection.execute(
        """
        INSERT INTO profile_entry (person_id, field, value, status, source_event_ids, turn_id,
            created_at)
        VALUES (%s, %s, %s, 'draft', %s, %s, %s)
        """,
        (person_id, field, value, source_event_ids, turn_id, now),
    )
    await connection.execute(
        """
        UPDATE introduction SET skipped = array_remove(skipped, %s), updated_at = %s
        WHERE person_id = %s
        """,
        (field, now, person_id),
    )


async def skip(connection: AsyncConnection, person_id: int, field: str, now: datetime) -> None:
    await connection.execute(
        "DELETE FROM profile_entry WHERE person_id = %s AND field = %s AND status = 'draft'",
        (person_id, field),
    )
    await connection.execute(
        """
        UPDATE introduction SET skipped = array_append(array_remove(skipped, %s), %s),
            updated_at = %s
        WHERE person_id = %s
        """,
        (field, field, now, person_id),
    )


async def propose(
    connection: AsyncConnection,
    person_id: int,
    field: str,
    value: str,
    *,
    source_event_ids: list[int],
    turn_id: int,
    now: datetime,
) -> None:
    """A stated change waiting for Confirm; it replaces an older unconfirmed one."""
    await connection.execute(
        "DELETE FROM profile_entry WHERE person_id = %s AND field = %s AND status = 'pending'",
        (person_id, field),
    )
    await connection.execute(
        """
        INSERT INTO profile_entry (person_id, field, value, status, source_event_ids, turn_id,
            created_at)
        VALUES (%s, %s, %s, 'pending', %s, %s, %s)
        """,
        (person_id, field, value, source_event_ids, turn_id, now),
    )


async def _activate(
    connection: AsyncConnection, person_id: int, entry: Entry, now: datetime
) -> bool:
    """Make a draft or pending entry active; False when it equals the active value."""
    current = await entries(connection, person_id, "active")
    active = next((item for item in current if item.field == entry.field), None)
    if active is not None and active.value == entry.value:
        await connection.execute("DELETE FROM profile_entry WHERE id = %s", (entry.id,))
        return False
    if active is not None:
        await connection.execute(
            "UPDATE profile_entry SET status = 'superseded', superseded_at = %s WHERE id = %s",
            (now, active.id),
        )
    await connection.execute(
        "UPDATE profile_entry SET status = 'active', confirmed_at = %s WHERE id = %s",
        (now, entry.id),
    )
    if entry.field == "timezone":
        await connection.execute(
            "UPDATE person SET timezone = %s WHERE id = %s", (entry.value, person_id)
        )
    return True


async def confirm_pending(
    connection: AsyncConnection, person_id: int, turn_id: int, now: datetime
) -> list[Entry] | None:
    """Activate the changes a reply proposed. None when none of them is still pending;
    otherwise the entries that changed a value."""
    pending = [
        entry
        for entry in await entries(connection, person_id, "pending")
        if entry.turn_id == turn_id
    ]
    if not pending:
        return None
    changed = [entry for entry in pending if await _activate(connection, person_id, entry, now)]
    for entry in pending:
        logger.info("Profile %s confirmed for person %d", entry.field, person_id)
    return changed


async def cancel_pending(connection: AsyncConnection, person_id: int, turn_id: int) -> bool:
    rows = await _rows(
        connection,
        """
        DELETE FROM profile_entry
        WHERE person_id = %s AND turn_id = %s AND status = 'pending'
        RETURNING field
        """,
        (person_id, turn_id),
    )
    for row in rows:
        logger.info("Profile %s change cancelled for person %d", row["field"], person_id)
    return bool(rows)


async def save_introduction(
    connection: AsyncConnection, person_id: int, now: datetime
) -> list[Entry] | None:
    """Activate every draft and complete the introduction. None when it is not active;
    otherwise the entries that changed a value."""
    current = await introduction(connection, person_id, lock=True)
    if current is None or current.state != "active":
        return None
    drafts = await entries(connection, person_id, "draft")
    changed = [entry for entry in drafts if await _activate(connection, person_id, entry, now)]
    await connection.execute(
        """
        UPDATE introduction SET state = 'completed', completed_at = %s, skipped = '{}',
            updated_at = %s
        WHERE person_id = %s
        """,
        (now, now, person_id),
    )
    logger.info("Introduction saved for person %d: %d values changed", person_id, len(changed))
    return changed


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
        await connection.execute("UPDATE person SET timezone = NULL WHERE id = %s", (person_id,))
    logger.info("Profile %s removed for person %d", field, person_id)
    return field


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
