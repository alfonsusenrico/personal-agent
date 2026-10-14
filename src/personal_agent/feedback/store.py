"""SQL for reply feedback: flags and notes on the assistant's messages.

Every function expects the caller's transaction, except the review queries, which
only read. Feedback refers to `message` rows, never to a channel's message: each
channel maps its own gesture (a Telegram 👎 or /note) to these operations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, LiteralString

from psycopg import AsyncConnection
from psycopg.rows import dict_row

Outcome = Literal["test", "dismissed"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OpenFlag:
    id: int
    kind: str
    note: str | None
    created_at: datetime
    owner_text: str | None
    reply_text: str


@dataclass(frozen=True, slots=True)
class Counts:
    open: int
    closed_test: int
    closed_dismissed: int
    withdrawn: int


async def _rows(
    connection: AsyncConnection, query: LiteralString, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    async with connection.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)
        return await cursor.fetchall()


async def flag(
    connection: AsyncConnection,
    person_id: int,
    message_id: int,
    *,
    source_event_id: int,
    now: datetime,
) -> bool:
    """Flag the reply. False when it already has an open flag."""
    rows = await _rows(
        connection,
        """
        INSERT INTO feedback (person_id, message_id, kind, source_event_id, created_at)
        VALUES (%s, %s, 'dislike', %s, %s)
        ON CONFLICT (message_id) WHERE kind = 'dislike' AND withdrawn_at IS NULL
            AND closed_at IS NULL
        DO NOTHING
        RETURNING id
        """,
        (person_id, message_id, source_event_id, now),
    )
    if rows:
        logger.info("Feedback %d dislike on message %d", rows[0]["id"], message_id)
    return bool(rows)


async def withdraw(
    connection: AsyncConnection, person_id: int, message_id: int, *, now: datetime
) -> bool:
    """Withdraw the reply's open flag. False when it has none."""
    rows = await _rows(
        connection,
        """
        UPDATE feedback SET withdrawn_at = %s
        WHERE person_id = %s AND message_id = %s AND kind = 'dislike'
          AND withdrawn_at IS NULL AND closed_at IS NULL
        RETURNING id
        """,
        (now, person_id, message_id),
    )
    if rows:
        logger.info("Feedback %d withdrawn", rows[0]["id"])
    return bool(rows)


async def note(
    connection: AsyncConnection,
    person_id: int,
    message_id: int,
    text: str,
    *,
    source_event_id: int,
    now: datetime,
) -> None:
    """Attach the owner's note to the reply."""
    rows = await _rows(
        connection,
        """
        INSERT INTO feedback (person_id, message_id, kind, note, source_event_id, created_at)
        VALUES (%s, %s, 'note', %s, %s, %s)
        RETURNING id
        """,
        (person_id, message_id, text, source_event_id, now),
    )
    logger.info("Feedback %d note on message %d", rows[0]["id"], message_id)


async def close(
    connection: AsyncConnection, feedback_id: int, outcome: Outcome, *, now: datetime
) -> bool:
    """Close an open flag or note with its outcome. False when it is not open."""
    rows = await _rows(
        connection,
        """
        UPDATE feedback SET closed_at = %s, outcome = %s
        WHERE id = %s AND withdrawn_at IS NULL AND closed_at IS NULL
        RETURNING id
        """,
        (now, outcome, feedback_id),
    )
    if rows:
        logger.info("Feedback %d closed as %s", feedback_id, outcome)
    return bool(rows)


async def open_flags(connection: AsyncConnection) -> list[OpenFlag]:
    """Open flags and notes, oldest first, with the owner's message and the reply."""
    rows = await _rows(
        connection,
        """
        SELECT f.id, f.kind, f.note, f.created_at, owner.text AS owner_text,
               reply.text AS reply_text
        FROM feedback f
        JOIN message reply ON reply.id = f.message_id
        LEFT JOIN message owner ON owner.turn_id = reply.turn_id AND owner.role = 'user'
        WHERE f.withdrawn_at IS NULL AND f.closed_at IS NULL
        ORDER BY f.created_at, f.id
        """,
    )
    return [
        OpenFlag(
            row["id"],
            row["kind"],
            row["note"],
            row["created_at"],
            row["owner_text"],
            row["reply_text"],
        )
        for row in rows
    ]


async def counts(connection: AsyncConnection) -> Counts:
    rows = await _rows(
        connection,
        """
        SELECT
            count(*) FILTER (WHERE withdrawn_at IS NULL AND closed_at IS NULL) AS open,
            count(*) FILTER (WHERE outcome = 'test') AS closed_test,
            count(*) FILTER (WHERE outcome = 'dismissed') AS closed_dismissed,
            count(*) FILTER (WHERE withdrawn_at IS NOT NULL) AS withdrawn
        FROM feedback
        """,
    )
    row = rows[0]
    return Counts(row["open"], row["closed_test"], row["closed_dismissed"], row["withdrawn"])
