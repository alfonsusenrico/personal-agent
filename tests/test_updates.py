from __future__ import annotations

from personal_agent.telegram.updates import parse_update
from tests.fakes import OWNER_ID, START, STRANGER_ID, message_update

ALLOWED = frozenset({OWNER_ID})


def test_owner_text_message_becomes_a_direct_message_event() -> None:
    envelope = parse_update(message_update(5, "hello", date=START), ALLOWED, now=START)

    assert envelope is not None
    assert (envelope.kind, envelope.modality, envelope.trust) == ("message", "text", "owner_direct")
    assert envelope.text == "hello"
    assert envelope.occurred_at == START
    assert envelope.answerable


def test_strangers_groups_and_bots_are_ignored() -> None:
    bot = message_update(3, "beep")
    bot["message"]["from"]["is_bot"] = True

    assert parse_update(message_update(1, "hi", user_id=STRANGER_ID), ALLOWED, now=START) is None
    assert parse_update(message_update(2, "hi", chat_type="group"), ALLOWED, now=START) is None
    assert parse_update(bot, ALLOWED, now=START) is None
    assert parse_update({"update_id": 4, "channel_post": {}}, ALLOWED, now=START) is None
    assert parse_update({"update_id": "broken"}, ALLOWED, now=START) is None


def test_edit_uses_the_edit_time() -> None:
    later = START.replace(minute=30)
    envelope = parse_update(
        message_update(6, "fixed", message_id=3, edit_date=later), ALLOWED, now=START
    )

    assert envelope is not None
    assert (envelope.kind, envelope.message_id, envelope.occurred_at) == ("edit", 3, later)


def test_media_is_unsupported_with_its_caption_as_text() -> None:
    photo = {"photo": [{"file_id": "x"}], "caption": "look"}
    envelope = parse_update(message_update(7, extra=photo), ALLOWED, now=START)

    assert envelope is not None
    assert (envelope.kind, envelope.modality, envelope.text) == ("unsupported", "photo", "look")


def test_forwarded_message_is_untrusted() -> None:
    origin = {"forward_origin": {"type": "hidden_user", "sender_user_name": "A", "date": 1}}
    envelope = parse_update(message_update(8, "fwd", extra=origin), ALLOWED, now=START)

    assert envelope is not None
    assert envelope.trust == "owner_forwarded"
    assert envelope.forward_origin == origin["forward_origin"]


def test_service_messages_are_logged_but_not_answered() -> None:
    envelope = parse_update(
        message_update(9, extra={"pinned_message": {"message_id": 1}}), ALLOWED, now=START
    )

    assert envelope is not None
    assert (envelope.modality, envelope.answerable) == ("service", False)


def _callback(
    update_id: int, *, user_id: int = OWNER_ID, chat_type: str = "private"
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "q1",
            "from": {"id": user_id, "is_bot": False},
            "message": {"message_id": 40, "date": 0, "chat": {"id": user_id, "type": chat_type}},
            "data": "b:0123456789abcdef",
        },
    }


def test_owner_button_press_becomes_a_button_event() -> None:
    envelope = parse_update(_callback(20), ALLOWED, now=START)

    assert envelope is not None
    assert (envelope.kind, envelope.message_id, envelope.button_token) == (
        "button",
        40,
        "b:0123456789abcdef",
    )
    assert (envelope.callback_query_id, envelope.occurred_at, envelope.answerable) == (
        "q1",
        START,
        True,
    )


def test_button_presses_from_strangers_or_groups_are_ignored() -> None:
    assert parse_update(_callback(21, user_id=STRANGER_ID), ALLOWED, now=START) is None
    assert parse_update(_callback(22, chat_type="group"), ALLOWED, now=START) is None


def test_poll_vote_and_stop_are_parsed_for_the_owner_only() -> None:
    vote = {
        "update_id": 23,
        "poll_answer": {
            "poll_id": "p1",
            "user": {"id": OWNER_ID, "is_bot": False},
            "option_ids": [1],
        },
    }
    stranger_vote = {
        "update_id": 24,
        "poll_answer": {
            "poll_id": "p1",
            "user": {"id": STRANGER_ID, "is_bot": False},
            "option_ids": [0],
        },
    }
    stop = {
        "update_id": 25,
        "stopped_message_generation": {"chat": {"id": OWNER_ID, "type": "private"}, "draft_id": 77},
    }

    parsed_vote = parse_update(vote, ALLOWED, now=START)
    parsed_stop = parse_update(stop, ALLOWED, now=START)

    assert parsed_vote is not None and parsed_vote.poll_option_ids == (1,)
    assert parse_update(stranger_vote, ALLOWED, now=START) is None
    assert parsed_stop is not None
    assert (parsed_stop.kind, parsed_stop.draft_id, parsed_stop.answerable) == ("stop", 77, False)
