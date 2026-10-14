"""The introduction, stated changes, /profile, and the bot's name, end to end: fake
Telegram server, real database, fake gateway."""

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

ALL_TOPICS = (
    '<pa-profile field="address">Sam</pa-profile>'
    '<pa-profile field="assistant_name">Nara</pa-profile>'
    '<pa-profile field="language">English, mixing Indonesian is fine</pa-profile>'
    '<pa-profile field="reply_style">Short and casual</pa-profile>'
    '<pa-profile field="timezone">Asia/Jakarta</pa-profile>'
    '<pa-profile field="work">Software engineer</pa-profile>'
    '<pa-profile field="routine">Up at 6, work 9 to 5</pa-profile>'
    '<pa-profile field="interests" skip="true"/>'
)


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
    return Agent(pool, server, responses, clock, introduction_declined=False)


@pytest.fixture
def declined(
    pool: AsyncConnectionPool,
    server: FakeTelegramServer,
    responses: FakeResponses,
    clock: FakeClock,
) -> Agent[FakeResponses]:
    """An owner who declined the introduction: profile changes come only from chat."""
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
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, update_id: int, label: str
) -> None:
    token, message_id = await _button(pool, label)
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


async def test_the_offer_comes_once_and_later_declines_it(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ["Morning! Want to do a quick introduction?", "Hi again."]
    await _say(agent, 1, "morning!")

    assert "offering a quick introduction" in agent.responses.developer_note()
    first = agent.server.sent_texts[0]
    assert "Start introduction" in first and "Later" in first

    await _press(agent, pool, 2, "Later")

    assert agent.server.edits[-1]["rich_message"]["markdown"].endswith("✓ Later")
    assert len(agent.responses.calls) == 1
    assert await _rows(pool, "SELECT state FROM introduction") == [("declined",)]

    await _say(agent, 3, "hi")

    assert "introduction" not in agent.responses.developer_note()
    assert "tg-button" not in agent.server.sent_texts[-1]


async def test_the_offer_keeps_only_the_buttons_that_start_it(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        "Halo! Mau mulai perkenalan singkat?\n<tg-button-row>"
        '<tg-button type="callback_data" data="go">Mulai</tg-button>'
        '<tg-button type="callback_data" data="later">Nanti</tg-button>'
        "</tg-button-row>"
    ]
    await _say(agent, 1, "halo")

    sent = agent.server.sent_texts[0]
    assert "Mulai" not in sent and "Nanti" not in sent
    assert "Start introduction" in sent
    labels = await _rows(pool, "SELECT label, action FROM reply_button ORDER BY id")
    assert labels == [("Start introduction", "intro_start"), ("Later", "intro_later")]


async def test_the_introduction_counts_only_after_save(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        "Morning! Want to do a quick introduction?",
        "Great. What should I call you?",
        f"Here's what I have.{ALL_TOPICS}",
        "All set, Sam. I'm Nara now.",
        "Hey Sam.",
    ]
    await _say(agent, 1, "morning!")
    await _press(agent, pool, 2, "Start introduction")

    assert "The introduction is in progress." in agent.responses.developer_note()
    assert "Topics still open, in order: What to call you (address), My name" in (
        agent.responses.developer_note()
    )
    assert agent.responses.user_content().endswith("[pressed] Start introduction")
    assert "ask the owner to confirm Asia/Jakarta" in agent.responses.developer_note()

    await _say(agent, 3, "Sam. Call yourself Nara. English. Short. Jakarta. Engineer. 6am. skip")

    summary = agent.server.sent_texts[-1]
    assert summary.startswith("Here's what I have.")
    assert "- **What to call you:** Sam\n- **My name:** Nara\n" in summary
    assert "Skipped: Interests" in summary
    assert "Save" in summary and "Edit" in summary
    assert await _active(pool) == []
    assert "# The owner's profile" not in agent.responses.calls[-1]["instructions"]

    await _press(agent, pool, 4, "Save")
    await agent.sender.send_pending()

    assert agent.server.edits[-1]["rich_message"]["markdown"].endswith("✓ Saved")
    assert "just saved the introduction" in agent.responses.developer_note()
    assert agent.server.sent_texts[-1] == "All set, Sam. I'm Nara now."
    assert ("address", "Sam") in await _active(pool)
    assert agent.server.bot_names == ["Nara"]
    assert await _rows(pool, "SELECT state FROM introduction") == [("completed",)]

    await _say(agent, 5, "hey")

    instructions = agent.responses.calls[-1]["instructions"]
    assert "- Your name: Nara" in instructions
    assert "- Call the owner: Sam" in instructions
    assert "introduction" not in agent.responses.developer_note()


async def test_edit_shows_a_new_summary_and_saves_nothing(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        "/start received. What should I call you?",
        f"Here's what I have.{ALL_TOPICS}",
        "Sure, what should change?",
        'Updated.<pa-profile field="address">Sammy</pa-profile>',
    ]
    await _say(agent, 1, "/start")
    await _say(agent, 2, "all the answers")
    await _press(agent, pool, 3, "Edit")

    assert "Every topic is answered." in agent.responses.developer_note()
    assert agent.server.sent_texts[-1] == "Sure, what should change?"

    await _say(agent, 4, "call me Sammy instead")

    assert "**What to call you:** Sammy" in agent.server.sent_texts[-1]
    assert await _active(pool) == []


async def test_start_after_saving_shows_the_profile_to_review(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        "/start received. What should I call you?",
        f"Here's what I have.{ALL_TOPICS}",
        "All set, Sam.",
        "Here's what I know about you; change anything you like.",
        'Sure.<pa-profile field="address">Sammy</pa-profile>',
    ]
    await _say(agent, 1, "/start")
    await _say(agent, 2, "all the answers")
    await _press(agent, pool, 3, "Save")
    await _say(agent, 4, "/start")

    review = agent.server.sent_texts[-1]
    assert review.startswith("Here's what I know about you")
    assert "- **What to call you:** Sam" in review and "Skipped: Interests" in review
    assert "Save" in review

    await _say(agent, 5, "call me Sammy again")

    assert "- **What to call you:** Sammy" in agent.server.sent_texts[-1]
    assert "Save" in agent.server.sent_texts[-1]


async def test_start_resumes_with_the_answers_so_far(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = [
        "What should I call you?",
        'Nice to meet you, Sam. What should my name be?<pa-profile field="address">Sam</pa-profile>',
        "Let's continue. What should my name be?",
    ]
    await _say(agent, 1, "/start")
    await _say(agent, 2, "Sam")
    await _say(agent, 3, "/start")

    note = agent.responses.developer_note()
    assert "Answered so far: What to call you: Sam." in note
    assert "Topics still open, in order: My name (assistant_name)" in note


async def test_a_stated_change_waits_for_confirm(
    declined: Agent[FakeResponses], pool: AsyncConnectionPool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    declined.responses.script = [
        'Sure, I can call you Sam.<pa-profile field="address">Sam</pa-profile>',
        "Okay.",
    ]
    await _say(declined, 1, "call me Sam from now on")

    sent = declined.server.sent_texts[-1]
    assert "**What to call you** → Sam" in sent and "Confirm" in sent and "Cancel" in sent
    assert await _active(pool) == []

    await _say(declined, 2, "thanks")

    assert "Waiting for the owner to confirm: What to call you: Sam." in (
        declined.responses.developer_note()
    )

    await _press(declined, pool, 3, "Confirm")

    assert declined.server.edits[-1]["rich_message"]["markdown"].endswith("✓ Saved")
    assert await _active(pool) == [("address", "Sam")]

    token, message_id = await _button(pool, "Cancel")
    await declined.receive(button_press(4, token, message_id=message_id))

    assert declined.server.callback_answers[-1]["text"] == UNAVAILABLE_CHOICE
    assert await _active(pool) == [("address", "Sam")]
    assert not any("Sam" in record.getMessage() for record in caplog.records)


async def test_a_newer_proposal_makes_the_older_confirm_stale(
    declined: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    declined.responses.script = [
        'Sam it is?<pa-profile field="address">Sam</pa-profile>',
        'Sammy then?<pa-profile field="address">Sammy</pa-profile>',
    ]
    await _say(declined, 1, "call me Sam")
    first_token, first_message = await _button(pool, "Confirm")
    await _say(declined, 2, "no, call me Sammy")

    await declined.receive(button_press(3, first_token, message_id=first_message))

    assert declined.server.callback_answers[-1]["text"] == UNAVAILABLE_CHOICE
    assert await _active(pool) == []
    assert await _rows(pool, "SELECT value, status FROM profile_entry") == [("Sammy", "pending")]


async def test_cancel_discards_the_change(
    declined: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    declined.responses.script = ['Short replies?<pa-profile field="reply_style">Short</pa-profile>']
    await _say(declined, 1, "keep replies shorter")
    await _press(declined, pool, 2, "Cancel")

    assert declined.server.edits[-1]["rich_message"]["markdown"].endswith("✓ Cancelled")
    assert await _rows(pool, "SELECT count(*) FROM profile_entry") == [(0,)]


async def test_profile_lists_entries_and_remove_forgets(
    declined: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    declined.responses.script = [
        'Noted?<pa-profile field="routine">Up at 6</pa-profile>',
        "Sure.",
    ]
    await _say(declined, 1, "I wake up at 6 every day, remember that")
    await _press(declined, pool, 2, "Confirm")
    calls = len(declined.responses.calls)

    await _say(declined, 3, "/profile")

    shown = declined.server.sent_texts[-1]
    assert "**Daily routine:** Up at 6 · 13 Oct 2026" in shown
    assert "Remove: Daily routine" in shown
    assert len(declined.responses.calls) == calls
    assert await _rows(
        pool, "SELECT text FROM message WHERE role = 'assistant' ORDER BY id DESC LIMIT 1"
    ) == [(PROFILE_SHOWN,)]

    await _press(declined, pool, 4, "Remove: Daily routine")

    assert declined.server.edits[-1]["rich_message"]["markdown"].endswith(
        "✓ Removed: Daily routine"
    )
    assert await _rows(pool, "SELECT count(*) FROM profile_entry") == [(0,)]

    await _say(declined, 5, "what time do I wake up?")

    assert "removed from their profile: Daily routine" in declined.responses.developer_note()

    await _say(declined, 6, "/profile")

    assert declined.server.sent_texts[-1] == EMPTY_PROFILE


async def test_a_confirmed_timezone_applies_to_new_messages(
    declined: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    declined.responses.script = ['Tokyo time?<pa-profile field="timezone">Asia/Tokyo</pa-profile>']
    await _say(declined, 1, "I'm in Tokyo this week")
    await _press(declined, pool, 2, "Confirm")
    await _say(declined, 3, "what time is it?")

    assert "(Asia/Tokyo, UTC+09:00)" in declined.responses.developer_note()
    rows = await _rows(pool, "SELECT person_tz FROM inbound_event ORDER BY id DESC LIMIT 1")
    assert rows == [("Asia/Tokyo",)]


async def test_a_refused_rename_keeps_the_saved_name(
    declined: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    declined.server.failures["setMyName"] = [bad_request("Bad Request: name is invalid")]
    declined.responses.script = ['Nara it is?<pa-profile field="assistant_name">Nara</pa-profile>']
    await _say(declined, 1, "your name is Nara")
    await _press(declined, pool, 2, "Confirm")
    await declined.sender.send_pending()

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
