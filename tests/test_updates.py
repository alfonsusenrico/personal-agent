from __future__ import annotations

from personal_agent.telegram.updates import parse_update
from tests.fakes import OWNER_ID, START, STRANGER_ID, message_update

ALLOWED = frozenset({OWNER_ID})


def test_owner_text_message_becomes_a_direct_message_event() -> None:
    envelope = parse_update(message_update(5, "hello", date=START), ALLOWED)

    assert envelope is not None
    assert (envelope.kind, envelope.modality, envelope.trust) == ("message", "text", "owner_direct")
    assert envelope.text == "hello"
    assert envelope.occurred_at == START
    assert envelope.answerable


def test_strangers_groups_and_bots_are_ignored() -> None:
    bot = message_update(3, "beep")
    bot["message"]["from"]["is_bot"] = True

    assert parse_update(message_update(1, "hi", user_id=STRANGER_ID), ALLOWED) is None
    assert parse_update(message_update(2, "hi", chat_type="group"), ALLOWED) is None
    assert parse_update(bot, ALLOWED) is None
    assert parse_update({"update_id": 4, "channel_post": {}}, ALLOWED) is None
    assert parse_update({"update_id": "broken"}, ALLOWED) is None


def test_edit_uses_the_edit_time() -> None:
    later = START.replace(minute=30)
    envelope = parse_update(message_update(6, "fixed", message_id=3, edit_date=later), ALLOWED)

    assert envelope is not None
    assert (envelope.kind, envelope.message_id, envelope.occurred_at) == ("edit", 3, later)


def test_media_is_unsupported_with_its_caption_as_text() -> None:
    photo = {"photo": [{"file_id": "x"}], "caption": "look"}
    envelope = parse_update(message_update(7, extra=photo), ALLOWED)

    assert envelope is not None
    assert (envelope.kind, envelope.modality, envelope.text) == ("unsupported", "photo", "look")


def test_forwarded_message_is_untrusted() -> None:
    origin = {"forward_origin": {"type": "hidden_user", "sender_user_name": "A", "date": 1}}
    envelope = parse_update(message_update(8, "fwd", extra=origin), ALLOWED)

    assert envelope is not None
    assert envelope.trust == "owner_forwarded"
    assert envelope.forward_origin == origin["forward_origin"]


def test_service_messages_are_logged_but_not_answered() -> None:
    envelope = parse_update(message_update(9, extra={"pinned_message": {"message_id": 1}}), ALLOWED)

    assert envelope is not None
    assert (envelope.modality, envelope.answerable) == ("service", False)
