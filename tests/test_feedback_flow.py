"""Reply feedback end to end: 👎 and /note against a fake Telegram server, the real
database, and a fake gateway."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from personal_agent.feedback import store as feedback_store
from tests.fakes import (
    START,
    FakeClock,
    FakeResponses,
    FakeTelegramServer,
    message_update,
    reaction_update,
)
from tests.harness import Agent

pytestmark = [pytest.mark.anyio, pytest.mark.db]

UNKNOWN_NOTE = "You don't know yet:"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def agent(pool: AsyncConnectionPool, clock: FakeClock) -> Agent[FakeResponses]:
    return Agent(pool, FakeTelegramServer(), FakeResponses(), clock)


async def _rows(
    pool: AsyncConnectionPool, query: Any, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    async with pool.connection() as connection:
        cursor = await connection.execute(query, params)
        return await cursor.fetchall()


async def _say(
    agent: Agent[FakeResponses],
    update_id: int,
    text: str,
    *,
    message_id: int | None = None,
    reply_to: int | None = None,
) -> None:
    agent.clock.advance(30)
    extra = {"reply_to_message": {"message_id": reply_to}} if reply_to else None
    await agent.receive(
        message_update(update_id, text, message_id=message_id, date=agent.clock.now, extra=extra)
    )
    await agent.settle()


async def _react(
    agent: Agent[FakeResponses], update_id: int, message_id: int, old: str = "", new: str = ""
) -> None:
    agent.clock.advance(5)
    await agent.receive(
        reaction_update(
            update_id,
            message_id,
            old=(old,) if old else (),
            new=(new,) if new else (),
            date=agent.clock.now,
        )
    )
    await agent.settle()


def _last_message_id(agent: Agent[FakeResponses]) -> int:
    return agent.server._next_message_id


async def _feedback(pool: AsyncConnectionPool) -> list[tuple[Any, ...]]:
    return await _rows(
        pool,
        """
        SELECT f.kind, f.note, f.withdrawn_at IS NOT NULL, m.text
        FROM feedback f JOIN message m ON m.id = f.message_id ORDER BY f.id
        """,
    )


async def test_thumbs_down_flags_once_and_removing_it_withdraws(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    agent.responses.script = ["Boil it for 9 minutes.", "Sure."]
    await _say(agent, 1, "how long to boil an egg?")
    reply = _last_message_id(agent)
    calls = len(agent.responses.calls)

    await _react(agent, 2, reply, new="👎")
    await _react(agent, 3, reply, old="👎", new="👎")

    assert await _feedback(pool) == [("dislike", None, False, "Boil it for 9 minutes.")]
    assert len(agent.responses.calls) == calls
    assert agent.server.sent[-1]["method"] == "sendRichMessage"

    await _react(agent, 4, reply, old="👎", new="👍")

    assert await _feedback(pool) == [("dislike", None, True, "Boil it for 9 minutes.")]
    events = await _rows(pool, "SELECT kind, turn_id FROM inbound_event WHERE kind = 'reaction'")
    assert events == [("reaction", None)] * 3
    assert not any("Boil" in record.getMessage() for record in caplog.records)

    await _say(agent, 5, "thanks")

    assert "👎" not in str(agent.responses.calls[-1]["input"])


async def test_a_reaction_on_the_owners_own_message_flags_nothing(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ["Hello."]
    await _say(agent, 1, "hi", message_id=77)

    await _react(agent, 2, 77, new="👎")

    assert await _feedback(pool) == []


async def test_any_part_of_a_split_reply_flags_the_whole_reply(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    long_reply = ("Step one is long. " * 1000).strip() + "\n\n" + ("Step two too. " * 1200).strip()
    agent.responses.script = [long_reply]
    await _say(agent, 1, "explain everything")
    rich = [item for item in agent.server.sent if item["method"] == "sendRichMessage"]
    assert len(rich) == 2

    await _react(agent, 2, _last_message_id(agent), new="👎")

    assert [row[:3] for row in await _feedback(pool)] == [("dislike", None, False)]
    assert (await _feedback(pool))[0][3] == long_reply


async def test_note_applies_to_the_pointed_reply_or_the_latest(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ["First answer.", "Second answer."]
    await _say(agent, 1, "first?")
    first = _last_message_id(agent)
    await _say(agent, 2, "second?")
    calls = len(agent.responses.calls)

    await _say(agent, 3, "/note too formal", message_id=300, reply_to=first)
    await _say(agent, 4, "/note wrong timezone", message_id=301)
    await _say(agent, 5, "/note", message_id=302)

    assert await _feedback(pool) == [
        ("note", "too formal", False, "First answer."),
        ("note", "wrong timezone", False, "Second answer."),
        ("dislike", None, False, "Second answer."),
    ]
    assert len(agent.responses.calls) == calls
    acknowledgements = [
        (item["message_id"], item["reaction"][0]["emoji"])
        for item in agent.server.sent
        if item["method"] == "setMessageReaction"
    ]
    assert acknowledgements == [(300, "👌"), (301, "👌"), (302, "👌")]
    assert await _rows(pool, "SELECT count(*) FROM turn") == [(2,)]


async def test_a_note_on_the_owners_message_applies_to_the_latest_reply(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ["Answer."]
    await _say(agent, 1, "question?", message_id=50)

    await _say(agent, 2, "/note missed my point", reply_to=50)

    assert await _feedback(pool) == [("note", "missed my point", False, "Answer.")]


async def test_a_note_before_any_reply_is_not_acknowledged(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    await _say(agent, 1, "/note hello")

    assert await _feedback(pool) == []
    assert agent.server.sent == []


async def test_feedback_does_not_count_toward_question_pacing(
    agent: Agent[FakeResponses],
) -> None:
    agent.responses.script = ['Morning! What should I call you?<pa-ask field="address"/>']
    await _say(agent, 1, "morning")
    for update_id in range(2, 7):
        await _say(agent, update_id, "/note fine")

    await _say(agent, 7, "ok")

    assert UNKNOWN_NOTE not in agent.responses.developer_note()


async def test_review_lists_open_feedback_with_its_context_and_closes_it(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool
) -> None:
    agent.responses.script = ["Nine minutes.", "Use a towel."]
    await _say(agent, 1, "egg?")
    await _say(agent, 2, "/note too short")
    await _say(agent, 3, "hands wet?")
    await _react(agent, 4, _last_message_id(agent), new="👎")

    async with pool.connection() as connection:
        flags = await feedback_store.open_flags(connection)
        assert [(flag.kind, flag.note, flag.owner_text, flag.reply_text) for flag in flags] == [
            ("note", "too short", "egg?", "Nine minutes."),
            ("dislike", None, "hands wet?", "Use a towel."),
        ]
        assert await feedback_store.close(connection, flags[0].id, "test", now=agent.clock.now)
        assert not await feedback_store.close(
            connection, flags[0].id, "dismissed", now=agent.clock.now
        )
        counts = await feedback_store.counts(connection)

    assert (counts.open, counts.closed_test, counts.closed_dismissed, counts.withdrawn) == (
        1,
        1,
        0,
        0,
    )
