"""The profile end to end: values saved with Undo, paced questions, /start, /profile,
and the bot's name, against a fake Telegram server, the real database, and a fake
gateway."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from personal_agent.profile.render import EMPTY_PROFILE, PROFILE_SHOWN
from personal_agent.telegram.commands import COMMANDS, sync_commands
from personal_agent.telegram.poller import UNAVAILABLE_CHOICE
from tests.fakes import (
    START,
    FakeClock,
    FakeResponses,
    FakeTelegramServer,
    bad_request,
    button_press,
    message_update,
)
from tests.harness import Agent

pytestmark = [pytest.mark.anyio, pytest.mark.db]

UNKNOWN_NOTE = "You don't know yet:"
ASKING_NOW_NOTE = "asked you (with /start) to get to know them now"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def server() -> FakeTelegramServer:
    return FakeTelegramServer()


@pytest.fixture
def responses() -> FakeResponses:
    return FakeResponses()


@pytest.fixture
def agent(
    pool: AsyncConnectionPool,
    server: FakeTelegramServer,
    responses: FakeResponses,
    clock: FakeClock,
) -> Agent[FakeResponses]:
    return Agent(pool, server, responses, clock)


async def _rows(
    pool: AsyncConnectionPool, query: Any, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    async with pool.connection() as connection:
        cursor = await connection.execute(query, params)
        return await cursor.fetchall()


async def _button(pool: AsyncConnectionPool, label: str) -> tuple[str, int]:
    """The newest button with this label: its token and its message's Telegram ID."""
    rows = await _rows(
        pool,
        """
        SELECT b.token, o.external_message_id FROM reply_button b
        JOIN outbox o ON o.id = b.outbox_id
        WHERE b.label = %s ORDER BY b.id DESC LIMIT 1
        """,
        (label,),
    )
    assert rows, f"no button {label!r}"
    token, message_id = rows[0]
    return token, int(message_id)


async def _press(
    agent: Agent[FakeResponses],
    pool: AsyncConnectionPool,
    update_id: int,
    label: str,
    button: tuple[str, int] | None = None,
) -> None:
    token, message_id = button or await _button(pool, label)
    agent.clock.advance(5)
    await agent.receive(button_press(update_id, token, message_id=message_id))
    await agent.settle()


async def _say(agent: Agent[FakeResponses], update_id: int, text: str) -> None:
    agent.clock.advance(30)
    await agent.receive(message_update(update_id, text, date=agent.clock.now))
    await agent.settle()


async def _active(pool: AsyncConnectionPool) -> list[tuple[Any, ...]]:
    return await _rows(
        pool, "SELECT field, value FROM profile_entry WHERE status = 'active' ORDER BY field"
    )


async def test_a_stated_value_is_saved_at_once_with_undo(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    agent.responses.script = ['Got it, Sam.<pa-profile field="address">Sam</pa-profile>', "Hi Sam."]
    await _say(agent, 1, "call me Sam from now on")

    sent = agent.server.sent_texts[-1]
    assert sent.startswith("Got it, Sam.\n\nSaved:\n- **What to call you** → Sam\n")
    assert ">Undo</tg-button>" in sent
    assert await _active(pool) == [("address", "Sam")]

    await _say(agent, 2, "hi")

    assert "- Call the owner: Sam" in agent.responses.calls[-1]["instructions"]
    assert not any("Sam" in record.getMessage() for record in caplog.records)


async def test_undo_restores_the_earlier_value_and_the_bot_name(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        'Nara it is.<pa-profile field="assistant_name">Nara</pa-profile>',
        'Kira then.<pa-profile field="assistant_name">Kira</pa-profile>',
    ]
    await _say(agent, 1, "your name is Nara")
    await _say(agent, 2, "no wait, call yourself Kira")
    undo = await _button(pool, "Undo")

    await _press(agent, pool, 3, "Undo", undo)

    assert agent.server.edits[-1]["rich_message"]["markdown"].endswith("✓ Undone")
    assert await _active(pool) == [("assistant_name", "Nara")]
    assert agent.server.bot_names == ["Nara", "Kira", "Nara"]
    assert len(agent.responses.calls) == 2

    await _press(agent, pool, 4, "Undo", undo)

    assert agent.server.callback_answers[-1]["text"] == UNAVAILABLE_CHOICE


async def test_undo_after_a_later_change_changes_nothing(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        'Sam.<pa-profile field="address">Sam</pa-profile>',
        'Sammy.<pa-profile field="address">Sammy</pa-profile>',
    ]
    await _say(agent, 1, "call me Sam")
    first_undo = await _button(pool, "Undo")
    await _say(agent, 2, "call me Sammy")

    await _press(agent, pool, 3, "Undo", first_undo)

    assert agent.server.callback_answers[-1]["text"] == UNAVAILABLE_CHOICE
    assert await _active(pool) == [("address", "Sammy")]


async def test_questions_are_paced_and_a_declined_topic_is_dropped(
    agent: Agent[FakeResponses],
) -> None:
    agent.responses.script = [
        'Morning! What does your usual day look like?<pa-ask field="routine"/>',
        'No problem.<pa-profile field="routine" skip="true"/>',
    ]
    await _say(agent, 1, "morning")

    assert UNKNOWN_NOTE in agent.responses.developer_note()

    for update_id in range(2, 7):
        await _say(agent, update_id, "not now" if update_id == 2 else "ok")
        assert UNKNOWN_NOTE not in agent.responses.developer_note()

    await _say(agent, 7, "ok")

    note = agent.responses.developer_note()
    assert UNKNOWN_NOTE in note
    assert "Daily routine" not in note


async def test_start_asks_until_a_reply_asks_nothing(agent: Agent[FakeResponses]) -> None:
    agent.responses.script = [
        'How do you like my replies?<pa-ask field="reply_style"/>',
        'Short it is. What do you do?<pa-profile field="reply_style">Short</pa-profile>'
        '<pa-ask field="work"/>',
        "5 miles is about 8 km.",
    ]
    await _say(agent, 1, "/start")

    assert ASKING_NOW_NOTE in agent.responses.developer_note()

    await _say(agent, 2, "short please")

    assert ASKING_NOW_NOTE in agent.responses.developer_note()

    await _say(agent, 3, "convert 5 miles to km?")
    await _say(agent, 4, "thanks")

    assert ASKING_NOW_NOTE not in agent.responses.developer_note()
    assert UNKNOWN_NOTE not in agent.responses.developer_note()


async def test_profile_lists_entries_and_remove_forgets(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ['Noted.<pa-profile field="routine">Up at 6</pa-profile>', "Sure."]
    await _say(agent, 1, "I wake up at 6 every day")
    calls = len(agent.responses.calls)

    await _say(agent, 2, "/profile")

    shown = agent.server.sent_texts[-1]
    assert "- **Daily routine:** Up at 6 · 13 Oct 2026" in shown
    assert "Remove: Daily routine" in shown
    assert len(agent.responses.calls) == calls
    last_assistant = await _rows(
        pool, "SELECT text FROM message WHERE role = 'assistant' ORDER BY id DESC LIMIT 1"
    )
    assert last_assistant == [(PROFILE_SHOWN,)]

    await _press(agent, pool, 3, "Remove: Daily routine")

    assert agent.server.edits[-1]["rich_message"]["markdown"].endswith("✓ Removed: Daily routine")
    assert await _rows(pool, "SELECT count(*) FROM profile_entry") == [(0,)]

    await _say(agent, 4, "what time do I wake up?")

    assert "removed from their profile: Daily routine" in agent.responses.developer_note()

    await _say(agent, 5, "/profile")

    assert agent.server.sent_texts[-1] == EMPTY_PROFILE


async def test_a_saved_timezone_applies_to_new_messages(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ['Tokyo time.<pa-profile field="timezone">Asia/Tokyo</pa-profile>']
    await _say(agent, 1, "I'm in Tokyo this week")
    await _say(agent, 2, "what time is it?")

    assert "(Asia/Tokyo, UTC+09:00)" in agent.responses.developer_note()
    rows = await _rows(pool, "SELECT person_tz FROM inbound_event ORDER BY id DESC LIMIT 1")
    assert rows == [("Asia/Tokyo",)]


async def test_a_refused_rename_keeps_the_saved_name(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.server.failures["setMyName"] = [bad_request("Bad Request: name is invalid")]
    agent.responses.script = ['Nara it is.<pa-profile field="assistant_name">Nara</pa-profile>']
    await _say(agent, 1, "your name is Nara")

    assert await _active(pool) == [("assistant_name", "Nara")]
    assert await _rows(pool, "SELECT status, error_code FROM outbox WHERE kind = 'bot_name'") == [
        ("failed", "http_400")
    ]


async def test_the_command_menu_is_replaced_once(server: FakeTelegramServer) -> None:
    server.commands = [{"command": "restart", "description": "Restart something else"}]
    api = server.api()

    await sync_commands(api)
    await sync_commands(api)

    assert server.commands == COMMANDS
    assert server.command_updates == 1
