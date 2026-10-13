"""Map Telegram updates to the channel-neutral inbound envelope.

Only private-chat messages from allowlisted users become envelopes; anything else is
ignored without being stored, because a stranger's message is their personal data.
"""

from __future__ import annotations

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

EventKind = Literal["message", "edit", "unsupported"]
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


class _Update(_Lenient):
    update_id: int
    message: _Message | None = None
    edited_message: _Message | None = None


@dataclass(frozen=True, slots=True)
class Envelope:
    update_id: int
    user_id: int
    chat_id: int
    message_id: int
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


def update_id_of(update: dict[str, Any]) -> int | None:
    value = update.get("update_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_update(update: dict[str, Any], allowed_user_ids: Iterable[int]) -> Envelope | None:
    """The envelope for an allowlisted private message, or None when it must be ignored."""
    try:
        parsed = _Update.model_validate(update)
    except ValidationError:
        return None
    message = parsed.message or parsed.edited_message
    if message is None or message.sender is None or message.sender.is_bot:
        return None
    if message.chat.type != "private" or message.sender.id not in set(allowed_user_ids):
        return None
    is_edit = parsed.edited_message is not None
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
        answerable=modality != "service",
    )


def _modality(message: dict[str, Any]) -> str:
    if message.get("text") is not None:
        return "text"
    for key in _UNREADABLE_CONTENT:
        if message.get(key) is not None:
            return key
    return "service"
