"""Map Telegram updates to the channel-neutral inbound envelope.

Only private-chat messages, button presses, poll votes, Stop presses, and reactions
from allowlisted users become envelopes; anything else is ignored without being
stored, because a stranger's message is their personal data.

Some Telegram clients send a Stop press as a typed /stop command instead of a
stopped_message_generation update, so /stop is a Stop press too, never a message.
/profile is a command answered by code; /start is a message that also asks the
assistant to get to know the owner now.

Reply feedback is mapped here, so the store sees only channel-neutral intents: 👎
added to a message flags it, 👎 removed withdraws the flag, and /note notes the
replied-to message or the latest reply, acknowledged with 👌.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Content the assistant cannot read yet, in Bot API field names. The first present
# key names the modality.
_UNREADABLE_CONTENT = (
    "photo",
    "voice",
    "audio",
    "video",
    "video_note",
    "sticker",
    "animation",
    "document",
    "location",
    "venue",
    "contact",
    "poll",
    "dice",
    "story",
    "paid_media",
)

_STOP_COMMAND = re.compile(r"/stop(@[A-Za-z0-9_]+)?")
_PROFILE_COMMAND = re.compile(r"/profile(@[A-Za-z0-9_]+)?")
_NOTE_COMMAND = re.compile(r"/note(?:@[A-Za-z0-9_]+)?(?:\s+(?P<note>.*))?", re.DOTALL)
# The texts that act as commands when sent: an edit to one is logged only.
_COMMAND_EDIT = re.compile(
    r"/(?:stop|profile)(?:@[A-Za-z0-9_]+)?|/note(?:@[A-Za-z0-9_]+)?(?:\s.*)?", re.DOTALL
)
DISLIKE = "👎"
NOTE_ACKNOWLEDGEMENT = "👌"

EventKind = Literal[
    "message", "edit", "unsupported", "button", "poll_answer", "stop", "command", "reaction"
]
FeedbackIntent = Literal["flag", "withdraw", "note"]
Trust = Literal["owner_direct", "owner_forwarded"]


class _Lenient(BaseModel):
    # Telegram adds fields over time; only the fields used here are validated.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Chat(_Lenient):
    id: int
    type: str


class _User(_Lenient):
    id: int
    is_bot: bool = False


class _RepliedMessage(_Lenient):
    message_id: int


class _Message(_Lenient):
    message_id: int
    date: int
    edit_date: int | None = None
    chat: _Chat
    sender: _User | None = Field(default=None, alias="from")
    text: str | None = None
    caption: str | None = None
    forward_origin: dict[str, Any] | None = None
    reply_to_message: _RepliedMessage | None = None


class _CallbackMessage(_Lenient):
    message_id: int
    chat: _Chat


class _CallbackQuery(_Lenient):
    id: str
    sender: _User = Field(alias="from")
    message: _CallbackMessage | None = None
    data: str | None = None


class _PollAnswer(_Lenient):
    poll_id: str
    user: _User | None = None
    option_ids: list[int]


class _ReactionType(_Lenient):
    type: str
    emoji: str | None = None


class _ReactionUpdated(_Lenient):
    chat: _Chat
    message_id: int
    user: _User | None = None
    date: int
    old_reaction: list[_ReactionType] = []
    new_reaction: list[_ReactionType] = []


class _GenerationStopped(_Lenient):
    chat: _Chat
    draft_id: int


class _Update(_Lenient):
    update_id: int
    message: _Message | None = None
    edited_message: _Message | None = None
    callback_query: _CallbackQuery | None = None
    poll_answer: _PollAnswer | None = None
    stopped_message_generation: _GenerationStopped | None = None
    message_reaction: _ReactionUpdated | None = None


@dataclass(frozen=True, slots=True)
class Envelope:
    update_id: int
    user_id: int
    chat_id: int
    # None for poll votes and Stop button presses, which carry no message.
    message_id: int | None
    kind: EventKind
    modality: str
    occurred_at: datetime
    trust: Trust
    forward_origin: dict[str, Any] | None
    reply_to_message_id: int | None
    text: str | None
    raw: dict[str, Any]
    # False for service messages (a pin, for example), which are logged but not answered.
    answerable: bool
    callback_query_id: str | None = None
    button_token: str | None = None
    poll_id: str | None = None
    poll_option_ids: tuple[int, ...] = ()
    # The stopped draft; None for a /stop command, which stops whatever is generating.
    draft_id: int | None = None
    # A command answered by code in its own turn.
    command: Literal["profile"] | None = None
    # Reply feedback: the target is this channel's message ID, or None for the latest
    # reply; an empty note is a flag.
    feedback: FeedbackIntent | None = None
    feedback_target_message_id: int | None = None
    feedback_note: str | None = None
    # The reaction that acknowledges the command message, if any.
    acknowledge_with: str | None = None


def update_id_of(update: dict[str, Any]) -> int | None:
    value = update.get("update_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_update(
    update: dict[str, Any], allowed_user_ids: Iterable[int], *, now: datetime
) -> Envelope | None:
    """The envelope for an allowlisted private update, or None when it must be ignored.

    `now` dates button presses, votes, and Stop presses, which Telegram does not date.
    """
    try:
        parsed = _Update.model_validate(update)
    except ValidationError:
        return None
    allowed = set(allowed_user_ids)
    if parsed.callback_query is not None:
        return _button_press(parsed.update_id, parsed.callback_query, allowed, now, update)
    if parsed.poll_answer is not None:
        return _poll_vote(parsed.update_id, parsed.poll_answer, allowed, now, update)
    if parsed.stopped_message_generation is not None:
        return _stop(parsed.update_id, parsed.stopped_message_generation, allowed, now, update)
    if parsed.message_reaction is not None:
        return _reaction(parsed.update_id, parsed.message_reaction, allowed, update)
    message = parsed.message or parsed.edited_message
    if message is None or message.sender is None or message.sender.is_bot:
        return None
    if message.chat.type != "private" or message.sender.id not in allowed:
        return None
    is_edit = parsed.edited_message is not None
    if not is_edit and message.text and _STOP_COMMAND.fullmatch(message.text.strip()):
        return _control_envelope(
            parsed.update_id,
            user_id=message.sender.id,
            kind="stop",
            now=now,
            raw=update,
            message_id=message.message_id,
            occurred_at=datetime.fromtimestamp(message.date, UTC),
            text=message.text,
        )
    if not is_edit and message.text and _PROFILE_COMMAND.fullmatch(message.text.strip()):
        return _control_envelope(
            parsed.update_id,
            user_id=message.sender.id,
            kind="command",
            now=now,
            raw=update,
            message_id=message.message_id,
            occurred_at=datetime.fromtimestamp(message.date, UTC),
            text="/profile",
            command="profile",
        )
    note = _NOTE_COMMAND.fullmatch(message.text.strip()) if message.text and not is_edit else None
    if note is not None:
        body = (note.group("note") or "").strip()
        return _control_envelope(
            parsed.update_id,
            user_id=message.sender.id,
            kind="command",
            now=now,
            raw=update,
            message_id=message.message_id,
            occurred_at=datetime.fromtimestamp(message.date, UTC),
            text=message.text,
            answerable=False,
            reply_to_message_id=(
                message.reply_to_message.message_id if message.reply_to_message else None
            ),
            feedback="note" if body else "flag",
            feedback_target_message_id=(
                message.reply_to_message.message_id if message.reply_to_message else None
            ),
            feedback_note=body or None,
            acknowledge_with=NOTE_ACKNOWLEDGEMENT,
        )
    modality = _modality(update["edited_message" if is_edit else "message"])
    if is_edit:
        kind: EventKind = "edit"
    elif modality == "text":
        kind = "message"
    else:
        kind = "unsupported"
    occurred = message.edit_date if is_edit and message.edit_date else message.date
    return Envelope(
        update_id=parsed.update_id,
        user_id=message.sender.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        kind=kind,
        modality=modality,
        occurred_at=datetime.fromtimestamp(occurred, UTC),
        trust="owner_forwarded" if message.forward_origin else "owner_direct",
        forward_origin=message.forward_origin or None,
        reply_to_message_id=(
            message.reply_to_message.message_id if message.reply_to_message else None
        ),
        text=message.text if message.text is not None else message.caption,
        raw=update,
        # An edited command is logged only: commands act when sent, never on an edit.
        answerable=modality != "service"
        and not (is_edit and message.text is not None and _is_command(message.text)),
    )


def _is_command(text: str) -> bool:
    return _COMMAND_EDIT.fullmatch(text.strip()) is not None


def _modality(message: dict[str, Any]) -> str:
    if message.get("text") is not None:
        return "text"
    for key in _UNREADABLE_CONTENT:
        if message.get(key) is not None:
            return key
    return "service"


def _control_envelope(
    update_id: int,
    *,
    user_id: int,
    kind: EventKind,
    now: datetime,
    raw: dict[str, Any],
    **fields: Any,
) -> Envelope:
    values: dict[str, Any] = {
        "update_id": update_id,
        "user_id": user_id,
        "chat_id": user_id,
        "message_id": None,
        "kind": kind,
        "modality": kind,
        "occurred_at": now,
        "trust": "owner_direct",
        "forward_origin": None,
        "reply_to_message_id": None,
        "text": None,
        "raw": raw,
        "answerable": kind != "stop",
    }
    values.update(fields)
    return Envelope(**values)


def _button_press(
    update_id: int, query: _CallbackQuery, allowed: set[int], now: datetime, raw: dict[str, Any]
) -> Envelope | None:
    if query.sender.is_bot or query.sender.id not in allowed or query.message is None:
        return None
    if query.message.chat.type != "private" or not query.data:
        return None
    return _control_envelope(
        update_id,
        user_id=query.sender.id,
        kind="button",
        now=now,
        raw=raw,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        callback_query_id=query.id,
        button_token=query.data,
    )


def _poll_vote(
    update_id: int, answer: _PollAnswer, allowed: set[int], now: datetime, raw: dict[str, Any]
) -> Envelope | None:
    # A vote without a user came from an anonymous voter; the assistant's polls are not.
    if answer.user is None or answer.user.id not in allowed:
        return None
    return _control_envelope(
        update_id,
        user_id=answer.user.id,
        kind="poll_answer",
        now=now,
        raw=raw,
        poll_id=answer.poll_id,
        poll_option_ids=tuple(answer.option_ids),
    )


def _stop(
    update_id: int,
    stopped: _GenerationStopped,
    allowed: set[int],
    now: datetime,
    raw: dict[str, Any],
) -> Envelope | None:
    # In a private chat the chat ID is the user's ID.
    if stopped.chat.type != "private" or stopped.chat.id not in allowed:
        return None
    return _control_envelope(
        update_id, user_id=stopped.chat.id, kind="stop", now=now, raw=raw, draft_id=stopped.draft_id
    )


def _reaction(
    update_id: int, updated: _ReactionUpdated, allowed: set[int], raw: dict[str, Any]
) -> Envelope | None:
    # Reactions from anonymous admins carry no user; only private chats are handled.
    if updated.chat.type != "private" or updated.user is None or updated.user.id not in allowed:
        return None
    before = _emojis(updated.old_reaction)
    after = _emojis(updated.new_reaction)
    feedback: FeedbackIntent | None = None
    if DISLIKE in after and DISLIKE not in before:
        feedback = "flag"
    elif DISLIKE in before and DISLIKE not in after:
        feedback = "withdraw"
    return _control_envelope(
        update_id,
        user_id=updated.user.id,
        kind="reaction",
        now=datetime.fromtimestamp(updated.date, UTC),
        raw=raw,
        chat_id=updated.chat.id,
        message_id=updated.message_id,
        answerable=False,
        feedback=feedback,
        feedback_target_message_id=updated.message_id,
    )


def _emojis(reactions: list[_ReactionType]) -> set[str]:
    return {reaction.emoji for reaction in reactions if reaction.emoji is not None}
