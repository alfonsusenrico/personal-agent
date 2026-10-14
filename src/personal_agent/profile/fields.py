"""The profile's fields: their order, labels, and the rules a value must meet."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Display and question order: names first, then how to talk, then the basics.
FIELDS = (
    "address",
    "assistant_name",
    "language",
    "reply_style",
    "timezone",
    "work",
    "routine",
    "interests",
)
# Shown in "Saved:" lines and /profile; the values are the owner's own words.
LABELS = {
    "address": "What to call you",
    "assistant_name": "My name",
    "language": "Language",
    "reply_style": "Reply style",
    "timezone": "Timezone",
    "work": "Work or study",
    "routine": "Daily routine",
    "interests": "Interests",
}
MAX_VALUE_CHARS = 300
# The Telegram bot name limit (setMyName).
MAX_NAME_CHARS = 64


def normalise(field: str, value: str) -> str | None:
    """The value to store, or None when the field is unknown or the value breaks a rule."""
    if field not in LABELS:
        return None
    text = " ".join(value.split())
    limit = MAX_NAME_CHARS if field == "assistant_name" else MAX_VALUE_CHARS
    if not text or len(text) > limit:
        return None
    if field == "timezone" and not _is_timezone(text):
        return None
    return text


def _is_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
