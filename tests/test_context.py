from __future__ import annotations

from datetime import timedelta

from personal_agent.conversation.context import (
    build_reply_request,
    describe_wait,
    render_turn,
    reply_instructions,
    select_window,
)
from personal_agent.conversation.models import EventRow, GenerationPlan, MessageRow, TurnInput
from personal_agent.profile.render import ProfileState, context_lines, open_topics
from personal_agent.profile.store import Entry, Topic
from tests.fakes import START


def _event(event_id: int, text: str | None, **fields: object) -> EventRow:
    values: dict[str, object] = {
        "id": event_id,
        "kind": "message",
        "modality": "text",
        "occurred_at": START,
        "trust": "owner_direct",
        "forward_origin": None,
        "text": text,
        "external_chat_id": "1001",
        "external_message_id": str(event_id),
        "edit_of": None,
    }
    values.update(fields)
    return EventRow(**values)  # pyright: ignore[reportArgumentType]


def _message(message_id: int, role: str, text: str) -> MessageRow:
    return MessageRow(message_id, role, text, START - timedelta(hours=1), "Asia/Jakarta")  # pyright: ignore[reportArgumentType]


def _turn(events: list[EventRow], window: list[MessageRow] | None = None) -> TurnInput:
    plan = GenerationPlan(
        turn_id=9,
        call_key="key",
        person_id=3,
        conversation_id=3,
        generation=1,
        started_at=START,
        through_event_id=9,
    )
    return TurnInput(plan, "Asia/Jakarta", "1001", events, window or [])


def test_layout_puts_stable_parts_first_and_time_only_in_the_note() -> None:
    window = [_message(1, "user", "earlier question"), _message(2, "assistant", "earlier answer")]

    request = build_reply_request(
        _turn([_event(5, "now")], window),
        window_budget_tokens=6_000,
        offline_after=timedelta(minutes=5),
    )

    assert request.instructions == reply_instructions()
    assert [item["role"] for item in request.input] == ["user", "assistant", "developer", "user"]
    assert request.input[0]["content"] == "[Tue 13 Oct 2026 13:05]\nearlier question"
    assert request.input[1]["content"] == "earlier answer"
    assert "Tuesday, 13 October 2026, 14:05" in request.input[2]["content"]
    assert request.input[3]["content"] == "[Tue 13 Oct 2026 14:05]\nnow"
    assert "2026" not in request.instructions
    assert request.prompt_cache_key == "person-3"


def test_window_keeps_the_newest_messages_within_budget() -> None:
    window = [_message(index, "user", "x" * 400) for index in range(100)]

    chosen = select_window(window, budget_tokens=500)

    assert [message.id for message in chosen] == [96, 97, 98, 99]


def test_window_holds_at_most_forty_messages() -> None:
    window = [_message(index, "user", "hi") for index in range(100)]

    assert len(select_window(window, budget_tokens=100_000)) == 40


def test_unreadable_media_and_captions_are_marked() -> None:
    rendered = render_turn(
        [
            _event(1, None, kind="unsupported", modality="voice"),
            _event(2, "is it ripe?", kind="unsupported", modality="photo"),
        ]
    )

    assert rendered.text == "[voice note]\n[photo] is it ripe?"
    assert rendered.unreadable == ["photo", "voice note"]


def test_repeated_edits_of_an_earlier_message_collapse() -> None:
    rendered = render_turn(
        [
            _event(7, "meet at 4", kind="edit", edit_of=1, previous_text="meet at 3"),
            _event(8, "meet at 5", kind="edit", edit_of=1, previous_text="meet at 4"),
        ]
    )

    assert rendered.text == "[edited an earlier message]\nBefore: meet at 3\nAfter: meet at 5"
    assert rendered.edits_earlier_message


def test_forward_names_cannot_break_the_tag() -> None:
    origin = {"type": "chat", "sender_chat": {"title": 'Evil"><b'}}
    rendered = render_turn([_event(1, "hi", trust="owner_forwarded", forward_origin=origin)])

    assert rendered.text == '<forwarded from="Evilb">\nhi\n</forwarded>'


def test_wait_descriptions() -> None:
    assert describe_wait(timedelta(hours=8, minutes=30)) == "8 hours 30 minutes"
    assert describe_wait(timedelta(days=2, hours=1, minutes=5)) == "2 days 1 hour"
    assert describe_wait(timedelta(seconds=20)) == "a few seconds"


def test_instructions_are_the_persona_then_the_telegram_guide() -> None:
    instructions = reply_instructions()

    assert instructions.startswith("You are the owner's personal assistant")
    assert "You are writing in Telegram." in instructions
    assert instructions.index("Telegram Rich Markdown") > instructions.index("How to talk:")
    assert "Plain text only" not in instructions


def _entry(field: str, value: str) -> Entry:
    return Entry(1, field, value, START, 1)


def test_the_saved_profile_follows_the_guide_and_stays_stable() -> None:
    active = [_entry("address", "Sam"), _entry("assistant_name", "Nara")]

    instructions = reply_instructions(active)

    assert instructions.startswith(reply_instructions().rstrip())
    assert instructions.index("# The owner's profile") > instructions.index(
        "Telegram Rich Markdown"
    )
    assert "- Your name: Nara" in instructions and "- Call the owner: Sam" in instructions
    assert reply_instructions(list(active)) == instructions


def test_what_is_unknown_is_shown_only_when_a_question_is_allowed() -> None:
    open_ = ["routine", "timezone"]

    paced = context_lines(ProfileState(open_topics=open_), tz="Asia/Jakarta")
    allowed = context_lines(
        ProfileState(open_topics=open_, question_allowed=True), tz="Asia/Jakarta"
    )
    now = context_lines(
        ProfileState(open_topics=open_, question_allowed=True, asking_now=True), tz="Asia/Jakarta"
    )

    assert paced == []
    assert "You don't know yet: Daily routine (routine), Timezone (timezone)." in allowed[0]
    assert "ask the owner to confirm Asia/Jakarta" in allowed[1]
    assert "asked you (with /start) to get to know them now" in now[0]


def test_the_profile_never_changes_the_instructions_mid_conversation() -> None:
    plan = GenerationPlan(1, "key", 1, 1, 1, START, 1)
    profile = ProfileState(open_topics=["routine"], question_allowed=True)
    turn = TurnInput(plan, "Asia/Jakarta", "1001", [_event(1, "hi")], [], profile)

    request = build_reply_request(
        turn, window_budget_tokens=6_000, offline_after=timedelta(hours=1)
    )

    assert request.instructions == reply_instructions()
    assert "You don't know yet: Daily routine (routine)." in request.input[-2]["content"]


def test_button_presses_and_votes_are_answers_not_unreadable_media() -> None:
    rendered = render_turn(
        [
            _event(1, "Sushi", kind="button", modality="button"),
            _event(2, "Dinner? → Pizza", kind="poll_answer", modality="poll_answer"),
        ]
    )

    assert rendered.text == "[pressed] Sushi\n[poll vote] Dinner? → Pizza"
    assert rendered.unreadable == []


def test_the_note_gives_unix_time_for_local_time_stamps() -> None:
    request = build_reply_request(
        _turn([_event(5, "when?")]), window_budget_tokens=6_000, offline_after=timedelta(minutes=5)
    )

    assert f"Unix time: {int(START.timestamp())}." in request.input[-2]["content"]


def test_a_topic_asked_twice_or_declined_is_not_open() -> None:
    asked = {
        "routine": Topic(2, START, False),
        "work": Topic(0, None, True),
        "interests": Topic(1, START, False),
    }

    open_ = open_topics([_entry("address", "Sam")], asked)

    assert open_ == ["assistant_name", "language", "reply_style", "timezone", "interests"]
