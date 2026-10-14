"""Telegram output end to end: rich messages, drafts, buttons, reactions, effects,
polls, media, and Stop, against the fake Telegram server and the real database."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from personal_agent.conversation.runner import STOPPED_RECORD
from personal_agent.telegram.poller import UNAVAILABLE_CHOICE
from personal_agent.telegram.rich import EFFECTS
from tests.fakes import (
    START,
    FakeClock,
    FakeResponses,
    FakeTelegramServer,
    bad_request,
    button_press,
    message_update,
    poll_vote,
    stop_press,
)
from tests.harness import Agent

pytestmark = [pytest.mark.anyio, pytest.mark.db]

CHOICES = (
    "Which one?\n<tg-button-row>\n"
    '<tg-button type="callback_data" data="a">Pizza</tg-button>\n'
    '<tg-button type="callback_data" data="b">Sushi</tg-button>\n'
    "</tg-button-row>"
)


class _WorkerStopped(Exception):
    pass


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


async def test_reply_streams_as_a_draft_then_arrives_as_rich_markdown(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    agent.responses.default_reply = "**Yes**, it's open until 9."
    await agent.receive(message_update(1, "is the shop open?", date=clock.now))
    await agent.settle()

    assert [item["method"] for item in agent.server.sent] == ["sendRichMessage"]
    assert agent.server.sent_texts == ["**Yes**, it's open until 9."]
    (draft_id,) = {draft for _chat, draft in agent.drafts.opened}
    texts = [text for draft, text in agent.drafts.updates if draft == draft_id]
    assert texts[-1] == "**Yes**, it's open until 9." and len(texts) >= 2
    stored = await _rows(pool, "SELECT text FROM message WHERE role = 'assistant'")
    assert stored == [("**Yes**, it's open until 9.",)]


async def test_rejected_rich_message_is_sent_once_as_plain_text(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    agent.server.failures["sendRichMessage"] = [bad_request("Bad Request: can't parse")]
    agent.responses.default_reply = "## Plan\n**Leave** at 8."
    await agent.receive(message_update(1, "plan?", date=clock.now))
    await agent.settle()

    assert [item["method"] for item in agent.server.sent] == ["sendMessage"]
    assert agent.server.sent_texts == ["Plan\nLeave at 8."]
    assert await _rows(pool, "SELECT kind, fallback_used FROM outbox") == [("rich", True)]


async def test_effect_is_sent_and_dropped_when_telegram_rejects_it(
    agent: Agent[FakeResponses], clock: FakeClock
) -> None:
    agent.responses.script = [
        '<pa-effect name="celebrate"/>Congratulations!',
        '<pa-effect name="love"/>Happy anniversary!',
    ]
    await agent.receive(message_update(1, "I got the job!", date=clock.now))
    await agent.settle()
    agent.server.failures["sendRichMessage"] = [bad_request("Bad Request: EFFECT_ID_INVALID")]
    clock.advance(60)
    await agent.receive(message_update(2, "and it's our anniversary", date=clock.now))
    await agent.settle()

    first, second = agent.server.messages
    assert first["message_effect_id"] == EFFECTS["celebrate"]
    assert first["rich_message"]["markdown"] == "Congratulations!"
    assert "message_effect_id" not in second
    assert second["rich_message"]["markdown"] == "Happy anniversary!"


async def test_a_reaction_alone_sends_no_message(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    agent.responses.default_reply = '<pa-react emoji="👍"/>'
    await agent.receive(message_update(1, "thanks, got it", message_id=12, date=clock.now))
    await agent.settle()

    assert agent.server.sent == [
        {
            "method": "setMessageReaction",
            "chat_id": "1001",
            "message_id": 12,
            "reaction": [{"type": "emoji", "emoji": "👍"}],
        }
    ]
    assert await _rows(pool, "SELECT status FROM turn") == [("answered",)]


async def test_only_media_the_owner_shared_is_sent(
    agent: Agent[FakeResponses], clock: FakeClock
) -> None:
    agent.responses.default_reply = (
        "Here it is:\n![](https://example.com/plan.png)\n![](https://made-up.example/x.png)"
    )
    await agent.receive(
        message_update(1, "show me https://example.com/plan.png again", date=clock.now)
    )
    await agent.settle()

    assert agent.server.sent_texts == ["Here it is:\n![](https://example.com/plan.png)"]


async def test_quick_reply_press_is_answered_and_used_once(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    agent.responses.script = [CHOICES, "Sushi it is."]
    await agent.receive(message_update(1, "dinner ideas?", date=clock.now))
    await agent.settle()
    sent_id = 5001
    buttons = dict(await _rows(pool, "SELECT label, token FROM reply_button ORDER BY position"))
    assert set(buttons) == {"Pizza", "Sushi"}
    assert buttons["Pizza"] in agent.server.sent_texts[0]

    clock.advance(10)
    await agent.receive(button_press(2, buttons["Sushi"], message_id=sent_id))
    await agent.settle()

    assert agent.server.callback_answers == [{"callback_query_id": "cq-2"}]
    (edit,) = agent.server.edits
    assert edit["message_id"] == sent_id
    assert edit["rich_message"]["markdown"].endswith("✓ Sushi")
    assert "callback_data" not in edit["rich_message"]["markdown"]
    assert agent.responses.user_content().endswith("[pressed] Sushi")
    assert agent.server.sent_texts[-1] == "Sushi it is."

    await agent.receive(button_press(3, buttons["Pizza"], message_id=sent_id))
    await agent.settle()

    assert agent.server.callback_answers[-1] == {
        "callback_query_id": "cq-3",
        "text": UNAVAILABLE_CHOICE,
    }
    assert len(agent.responses.calls) == 2
    kinds = await _rows(pool, "SELECT kind, turn_id IS NULL FROM inbound_event ORDER BY id")
    assert kinds == [("message", False), ("button", False), ("button", True)]


async def test_poll_is_sent_after_the_message_and_votes_come_back(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    agent.responses.script = [
        'Vote here.\n<pa-poll question="Friday dinner?" multiple="false">'
        "<option>Pizza</option><option>Sushi</option></pa-poll>",
        "Sushi wins.",
    ]
    await agent.receive(message_update(1, "make a poll for dinner", date=clock.now))
    await agent.settle()

    assert [item["method"] for item in agent.server.sent] == ["sendRichMessage", "sendPoll"]
    (poll_id,) = (await _rows(pool, "SELECT external_poll_id FROM reply_poll"))[0]
    clock.advance(30)
    await agent.receive(poll_vote(2, poll_id, [1]))
    await agent.settle()
    await agent.receive(poll_vote(3, poll_id, []))
    await agent.settle()

    assert agent.responses.user_content().endswith("[poll vote] Friday dinner? → Sushi")
    assert agent.server.sent_texts[-1] == "Sushi wins."
    assert len(agent.responses.calls) == 2


async def test_a_poll_alone_is_sent_without_a_message(
    agent: Agent[FakeResponses], clock: FakeClock
) -> None:
    agent.responses.default_reply = 'Which team?\n<pa-poll question="Which team?"><option>A</option><option>B</option></pa-poll>'
    await agent.receive(message_update(1, "poll: A or B", date=clock.now))
    await agent.settle()

    assert [item["method"] for item in agent.server.sent] == ["sendPoll"]


async def test_stop_discards_the_reply_and_the_next_reply_knows(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    async def owner_presses_stop(body: dict[str, Any]) -> None:
        if len(agent.responses.calls) == 1:
            _chat, draft_id = agent.drafts.opened[-1]
            await agent.receive(stop_press(2, draft_id))

    agent.responses.on_call = owner_presses_stop
    agent.responses.script = ["a very long answer that the owner stops", "Sure, shorter then."]
    await agent.receive(message_update(1, "explain quantum physics", date=clock.now))
    await agent.settle()

    assert agent.server.sent == []
    assert await _rows(pool, "SELECT status FROM turn") == [("stopped",)]
    assert await _rows(pool, "SELECT status FROM model_call") == [("stopped",)]

    clock.advance(30)
    await agent.receive(message_update(3, "short version please", date=clock.now))
    await agent.settle()

    window = [item["content"] for item in agent.responses.calls[-1]["input"]]
    assert STOPPED_RECORD in window
    assert agent.server.sent_texts == ["Sure, shorter then."]


async def test_a_typed_stop_command_stops_the_reply(
    agent: Agent[FakeResponses],
    pool: AsyncConnectionPool,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def owner_types_stop(body: dict[str, Any]) -> None:
        if len(agent.responses.calls) == 1:
            await agent.receive(message_update(2, "/stop", date=clock.now))

    agent.responses.on_call = owner_types_stop
    await agent.receive(message_update(1, "explain the internet in detail", date=clock.now))
    await agent.settle()

    assert agent.server.sent == []
    assert len(agent.responses.calls) == 1
    assert await _rows(pool, "SELECT status FROM turn") == [("stopped",)]
    assert await _rows(pool, "SELECT text FROM message WHERE role = 'user'") == [
        ("explain the internet in detail",)
    ]
    # Stopping is the owner's choice, not a fault worth a warning.
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


async def test_a_stop_command_with_nothing_running_is_not_answered(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    await agent.receive(message_update(1, "/stop", date=clock.now))
    await agent.settle()

    assert agent.server.sent == []
    assert agent.responses.calls == []
    assert await _rows(pool, "SELECT kind, turn_id FROM inbound_event") == [("stop", None)]


async def test_stop_for_another_draft_is_ignored(
    agent: Agent[FakeResponses], clock: FakeClock
) -> None:
    async def stale_stop(body: dict[str, Any]) -> None:
        if len(agent.responses.calls) == 1:
            await agent.receive(stop_press(2, 999))

    agent.responses.on_call = stale_stop
    await agent.receive(message_update(1, "hi", date=clock.now))
    await agent.settle()

    assert agent.server.sent_texts == ["Okay."]


async def test_stop_recorded_before_a_restart_still_closes_the_turn(
    pool: AsyncConnectionPool,
    server: FakeTelegramServer,
    responses: FakeResponses,
    clock: FakeClock,
) -> None:
    async def worker_dies(body: dict[str, Any]) -> None:
        raise _WorkerStopped

    first = Agent(pool, server, responses, clock)
    responses.on_call = worker_dies
    await first.receive(message_update(1, "long question", date=clock.now))
    clock.advance(3)
    with pytest.raises(_WorkerStopped):
        await first.runner.step()
    (draft_id,) = (await _rows(pool, "SELECT draft_id FROM turn"))[0]

    responses.on_call = None
    restarted = Agent(pool, server, responses, clock)
    await restarted.receive(stop_press(2, draft_id))
    await restarted.settle()

    assert server.sent == []
    assert len(responses.calls) == 1
    assert await _rows(pool, "SELECT status FROM turn") == [("stopped",)]


async def test_regeneration_replaces_the_visible_draft(
    agent: Agent[FakeResponses], clock: FakeClock
) -> None:
    async def follow_up(body: dict[str, Any]) -> None:
        if len(agent.responses.calls) == 1:
            await agent.receive(message_update(2, "and tomorrow?", date=clock.now))

    agent.responses.on_call = follow_up
    await agent.receive(message_update(1, "free today?", date=clock.now))
    await agent.settle()

    draft_ids = [draft for _chat, draft in agent.drafts.opened]
    assert len(draft_ids) == 2 and draft_ids[0] != draft_ids[1]
    assert len(agent.server.sent) == 1


async def test_a_reaction_to_a_button_press_arrives_as_the_emoji(
    agent: Agent[FakeResponses], pool: AsyncConnectionPool, clock: FakeClock
) -> None:
    agent.responses.script = [CHOICES, '<pa-react emoji="👍"/>']
    await agent.receive(message_update(1, "dinner ideas?", date=clock.now))
    await agent.settle()
    token = (await _rows(pool, "SELECT token FROM reply_button WHERE label = 'Sushi'"))[0][0]
    await agent.receive(button_press(2, token, message_id=5001))
    await agent.settle()

    assert "needs a typed message" in agent.responses.developer_note()
    assert agent.server.sent_texts[-1] == "👍"
    assert "setMessageReaction" not in [item["method"] for item in agent.server.sent]
