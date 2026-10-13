from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 13, tzinfo=UTC)


def _seed(connection: psycopg.Connection) -> tuple[int, int, int]:
    person = connection.execute(
        "INSERT INTO person (created_at) VALUES (%s) RETURNING id", (NOW,)
    ).fetchone()
    assert person is not None
    conversation = connection.execute(
        "INSERT INTO conversation (person_id, kind, created_at) VALUES (%s, 'main', %s) RETURNING id",
        (person[0], NOW),
    ).fetchone()
    assert conversation is not None
    turn = connection.execute(
        "INSERT INTO turn (person_id, conversation_id, status, opened_at) "
        "VALUES (%s, %s, 'collecting', %s) RETURNING id",
        (person[0], conversation[0], NOW),
    ).fetchone()
    assert turn is not None
    return person[0], conversation[0], turn[0]


def _insert_event(
    connection: psycopg.Connection, ids: tuple[int, int, int], update_id: str
) -> None:
    person_id, conversation_id, turn_id = ids
    connection.execute(
        """
        INSERT INTO inbound_event (
            person_id, conversation_id, turn_id, channel, external_update_id, external_chat_id,
            external_message_id, kind, modality, occurred_at, received_at, person_tz, trust,
            text, raw
        ) VALUES (%s, %s, %s, 'telegram', %s, '1', '1', 'message', 'text', %s, %s,
            'Asia/Jakarta', 'owner_direct', 'hi', '{}')
        """,
        (person_id, conversation_id, turn_id, update_id, NOW, NOW),
    )


@pytest.fixture
def connection(test_database_url: str, migrated_schema: str):
    with psycopg.connect(
        test_database_url, options=f"-c search_path={migrated_schema}", autocommit=True
    ) as connection:
        yield connection


def test_an_update_is_recorded_once(connection: psycopg.Connection) -> None:
    ids = _seed(connection)
    _insert_event(connection, ids, "42")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_event(connection, ids, "42")


def test_logged_events_cannot_be_changed(connection: psycopg.Connection) -> None:
    _insert_event(connection, _seed(connection), "1")

    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        connection.execute("UPDATE inbound_event SET text = 'rewritten'")


def test_one_collecting_turn_per_conversation(connection: psycopg.Connection) -> None:
    person_id, conversation_id, _ = _seed(connection)

    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO turn (person_id, conversation_id, status, opened_at) "
            "VALUES (%s, %s, 'collecting', %s)",
            (person_id, conversation_id, NOW),
        )


def test_forwarded_trust_requires_an_origin(connection: psycopg.Connection) -> None:
    person_id, conversation_id, turn_id = _seed(connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            """
            INSERT INTO inbound_event (
                person_id, conversation_id, turn_id, channel, external_update_id,
                external_chat_id, external_message_id, kind, modality, occurred_at,
                received_at, person_tz, trust, raw
            ) VALUES (%s, %s, %s, 'telegram', '9', '1', '1', 'message', 'text', %s, %s,
                'Asia/Jakarta', 'owner_forwarded', '{}')
            """,
            (person_id, conversation_id, turn_id, NOW, NOW),
        )
