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


def test_a_quick_reply_token_is_unique(connection: psycopg.Connection) -> None:
    person_id, conversation_id, turn_id = _seed(connection)
    message = connection.execute(
        """
        INSERT INTO message (person_id, conversation_id, turn_id, role, text, occurred_at,
            person_tz, trust, source_event_ids, created_at)
        VALUES (%s, %s, %s, 'assistant', 'x', %s, 'Asia/Jakarta', 'assistant', '{}', %s)
        RETURNING id
        """,
        (person_id, conversation_id, turn_id, NOW, NOW),
    ).fetchone()
    assert message is not None
    outbox = connection.execute(
        """
        INSERT INTO outbox (person_id, turn_id, message_id, channel, external_chat_id,
            chunk_index, kind, text, status, created_at)
        VALUES (%s, %s, %s, 'telegram', '1', 0, 'rich', 'x', 'pending', %s) RETURNING id
        """,
        (person_id, turn_id, message[0], NOW),
    ).fetchone()
    assert outbox is not None
    insert = (
        "INSERT INTO reply_button (person_id, turn_id, outbox_id, token, label, position, created_at)"
        " VALUES (%s, %s, %s, 'b:1', 'A', 0, %s)"
    )
    connection.execute(insert, (person_id, turn_id, outbox[0], NOW))

    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(insert, (person_id, turn_id, outbox[0], NOW))


def test_reactions_carry_no_text_and_messages_must(connection: psycopg.Connection) -> None:
    person_id, conversation_id, turn_id = _seed(connection)
    message = connection.execute(
        """
        INSERT INTO message (person_id, conversation_id, turn_id, role, text, occurred_at,
            person_tz, trust, source_event_ids, created_at)
        VALUES (%s, %s, %s, 'assistant', 'x', %s, 'Asia/Jakarta', 'assistant', '{}', %s)
        RETURNING id
        """,
        (person_id, conversation_id, turn_id, NOW, NOW),
    ).fetchone()
    assert message is not None

    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            """
            INSERT INTO outbox (person_id, turn_id, message_id, channel, external_chat_id,
                chunk_index, kind, text, status, created_at)
            VALUES (%s, %s, %s, 'telegram', '1', 0, 'rich', NULL, 'pending', %s)
            """,
            (person_id, turn_id, message[0], NOW),
        )


def _profile_entry(
    connection: psycopg.Connection,
    person_id: int,
    *,
    field: str = "address",
    value: str = "Sam",
    status: str = "active",
) -> None:
    superseded = NOW if status == "superseded" else None
    connection.execute(
        """
        INSERT INTO profile_entry (person_id, field, value, status, source_event_ids,
            saved_at, superseded_at)
        VALUES (%s, %s, %s, %s, '{}', %s, %s)
        """,
        (person_id, field, value, status, NOW, superseded),
    )


def test_a_field_has_one_active_value_and_keeps_history(connection: psycopg.Connection) -> None:
    person_id, _, _ = _seed(connection)
    _profile_entry(connection, person_id, value="Samuel", status="superseded")
    _profile_entry(connection, person_id, value="Sam")

    with pytest.raises(psycopg.errors.UniqueViolation):
        _profile_entry(connection, person_id, value="Another")


def test_profile_values_are_bounded_and_known(connection: psycopg.Connection) -> None:
    person_id, _, _ = _seed(connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        _profile_entry(connection, person_id, field="favourite_colour")
    with pytest.raises(psycopg.errors.CheckViolation):
        _profile_entry(connection, person_id, field="assistant_name", value="x" * 65)
    with pytest.raises(psycopg.errors.CheckViolation):
        _profile_entry(connection, person_id, field="work", value="x" * 301)
    with pytest.raises(psycopg.errors.CheckViolation):
        _profile_entry(connection, person_id, status="draft")


def test_an_action_button_needs_its_target(connection: psycopg.Connection) -> None:
    person_id, conversation_id, turn_id = _seed(connection)
    message = connection.execute(
        """
        INSERT INTO message (person_id, conversation_id, turn_id, role, text, occurred_at,
            person_tz, trust, source_event_ids, created_at)
        VALUES (%s, %s, %s, 'assistant', 'x', %s, 'Asia/Jakarta', 'assistant', '{}', %s)
        RETURNING id
        """,
        (person_id, conversation_id, turn_id, NOW, NOW),
    ).fetchone()
    assert message is not None
    outbox = connection.execute(
        """
        INSERT INTO outbox (person_id, turn_id, message_id, channel, external_chat_id,
            chunk_index, kind, text, status, created_at)
        VALUES (%s, %s, %s, 'telegram', '1', 0, 'rich', 'x', 'pending', %s) RETURNING id
        """,
        (person_id, turn_id, message[0], NOW),
    ).fetchone()
    assert outbox is not None
    insert = (
        "INSERT INTO reply_button (person_id, turn_id, outbox_id, token, label, position,"
        " created_at, action, target_id) VALUES (%s, %s, %s, %s, 'Undo', 0, %s, %s, %s)"
    )
    connection.execute(insert, (person_id, turn_id, outbox[0], "b:1", NOW, "profile_undo", turn_id))

    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            insert, (person_id, turn_id, outbox[0], "b:2", NOW, "profile_undo", None)
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(insert, (person_id, turn_id, outbox[0], "b:3", NOW, "dance", 1))


def test_only_the_bot_name_is_sent_without_a_turn(connection: psycopg.Connection) -> None:
    person_id, _, _ = _seed(connection)
    insert = (
        "INSERT INTO outbox (person_id, channel, external_chat_id, chunk_index, kind, text,"
        " status, created_at) VALUES (%s, 'telegram', '1', 0, %s, 'Nara', 'pending', %s)"
    )
    connection.execute(insert, (person_id, "bot_name", NOW))

    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(insert, (person_id, "rich", NOW))


def test_a_topic_is_counted_once_per_person(connection: psycopg.Connection) -> None:
    person_id, _, _ = _seed(connection)
    insert = "INSERT INTO profile_topic (person_id, field, asks) VALUES (%s, 'routine', %s)"
    connection.execute(insert, (person_id, 1))

    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(insert, (person_id, 1))
    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            "INSERT INTO profile_topic (person_id, field, asks) VALUES (%s, 'work', -1)",
            (person_id,),
        )
