from __future__ import annotations

from personal_agent.telegram.updates import parse_update
from tests.fakes import OWNER_ID, START, STRANGER_ID, message_update, reaction_update

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


def test_a_stop_command_is_a_stop_press_not_a_message() -> None:
    command = parse_update(message_update(26, "/stop", message_id=9), ALLOWED, now=START)
    addressed = parse_update(message_update(27, "/stop@helper_bot"), ALLOWED, now=START)
    sentence = parse_update(message_update(28, "/stop talking about it"), ALLOWED, now=START)
    stranger = parse_update(message_update(29, "/stop", user_id=STRANGER_ID), ALLOWED, now=START)

    assert command is not None
    assert (command.kind, command.draft_id, command.answerable) == ("stop", None, False)
    assert (command.message_id, command.text) == (9, "/stop")
    assert addressed is not None and addressed.kind == "stop"
    assert sentence is not None and sentence.kind == "message"
    assert stranger is None


def test_a_thumbs_down_flags_and_removing_it_withdraws() -> None:
    added = parse_update(reaction_update(40, 900, new=("👎",)), ALLOWED, now=START)
    removed = parse_update(reaction_update(41, 900, old=("👎",), new=("👍",)), ALLOWED, now=START)
    other = parse_update(reaction_update(42, 900, new=("❤",)), ALLOWED, now=START)
    kept = parse_update(reaction_update(43, 900, old=("👎",), new=("👎", "🔥")), ALLOWED, now=START)

    assert added is not None
    assert (added.kind, added.answerable, added.text) == ("reaction", False, None)
    assert (added.feedback, added.feedback_target_message_id, added.message_id) == (
        "flag",
        900,
        900,
    )
    assert removed is not None and removed.feedback == "withdraw"
    assert other is not None and other.feedback is None
    assert kept is not None and kept.feedback is None


def test_reactions_from_strangers_and_groups_are_ignored() -> None:
    stranger = reaction_update(44, 900, new=("👎",), user_id=STRANGER_ID)
    group = reaction_update(45, 900, new=("👎",), chat_type="group")
    anonymous = reaction_update(46, 900, new=("👎",))
    del anonymous["message_reaction"]["user"]

    assert parse_update(stranger, ALLOWED, now=START) is None
    assert parse_update(group, ALLOWED, now=START) is None
    assert parse_update(anonymous, ALLOWED, now=START) is None


def test_note_is_feedback_on_the_replied_message_or_the_latest() -> None:
    replied = message_update(50, "/note too formal", extra={"reply_to_message": {"message_id": 7}})
    plain = parse_update(
        message_update(51, "/note@helper_bot  wrong\ntimezone"), ALLOWED, now=START
    )
    bare = parse_update(message_update(52, "/note"), ALLOWED, now=START)
    other = parse_update(message_update(53, "/notes are useful"), ALLOWED, now=START)

    note = parse_update(replied, ALLOWED, now=START)

    assert note is not None
    assert (note.kind, note.answerable, note.command) == ("command", False, None)
    assert (note.feedback, note.feedback_note, note.feedback_target_message_id) == (
        "note",
        "too formal",
        7,
    )
    assert note.text == "/note too formal"
    assert note.acknowledge_with == "👌"
    assert plain is not None and (plain.feedback_note, plain.feedback_target_message_id) == (
        "wrong\ntimezone",
        None,
    )
    assert bare is not None and (bare.feedback, bare.feedback_note) == ("flag", None)
    assert other is not None and (other.kind, other.feedback) == ("message", None)


def test_an_edited_command_is_logged_but_not_answered() -> None:
    later = START.replace(minute=30)
    edits = {
        text: parse_update(message_update(60 + index, text, edit_date=later), ALLOWED, now=START)
        for index, text in enumerate(["/note longer note", "/profile", "/stop", "/stop it now"])
    }

    assert all(envelope is not None and envelope.kind == "edit" for envelope in edits.values())
    answerable = {text: envelope.answerable for text, envelope in edits.items() if envelope}
    assert answerable == {
        "/note longer note": False,
        "/profile": False,
        "/stop": False,
        "/stop it now": True,
    }
