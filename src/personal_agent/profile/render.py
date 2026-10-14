"""Text the profile adds to replies and prompts: rendered by code from stored rows,
so the owner always sees exactly what is stored."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from personal_agent.profile.fields import FIELDS, LABELS
from personal_agent.profile.store import Entry, Topic

# Stored as the assistant's message for /profile, so profile values do not linger in
# the recent window after a removal.
PROFILE_SHOWN = "[the owner's profile was shown]"
EMPTY_PROFILE = "Your profile is empty. Tell me anything you'd like me to remember, or send /start."
CHANGE_HINT = "To change something, just tell me."
UNDO_LABEL = "Undo"
# How each saved value is named in the model's instructions.
_PROMPT_LABELS = {
    "assistant_name": "Your name",
    "address": "Call the owner",
    "language": "Language",
    "reply_style": "Reply style",
    "work": "The owner's work or study",
    "routine": "The owner's daily routine",
    "interests": "The owner's interests",
}


@dataclass(frozen=True, slots=True)
class Button:
    label: str
    action: str
    target_id: int


@dataclass(frozen=True, slots=True)
class ProfileState:
    """What the reply call and its commit need to know about the profile."""

    active: list[Entry] = field(default_factory=list)
    # Topics with no value, not declined, and asked fewer than twice, in order.
    open_topics: list[str] = field(default_factory=list)
    # /start asked for the open topics now.
    asking_now: bool = False
    # Pacing allows a getting-to-know question in this reply.
    question_allowed: bool = False
    # Fields removed since the oldest message in the recent window.
    removed: list[str] = field(default_factory=list)


# Rich Markdown joins single line breaks into one paragraph, so stored values are
# list items and blocks are separated by blank lines.
def saved_text(saved: list[tuple[str, str]]) -> str:
    """The values a reply saved, as (field, value) pairs."""
    items = "\n".join(f"- **{LABELS[name]}** → {value}" for name, value in saved)
    return f"Saved:\n{items}"


def profile_text(active: list[Entry], tz: str) -> str:
    if not active:
        return EMPTY_PROFILE
    zone = ZoneInfo(tz)
    lines = [
        f"- **{LABELS[entry.field]}:** {entry.value} · {entry.saved_at.astimezone(zone):%-d %b %Y}"
        for entry in active
    ]
    return "\n\n".join(["**Your profile**", "\n".join(lines), CHANGE_HINT])


def remove_buttons(active: list[Entry]) -> list[list[Button]]:
    return [
        [Button(f"Remove: {LABELS[entry.field]}", "profile_remove", entry.id)] for entry in active
    ]


def undo_button(turn_id: int) -> list[list[Button]]:
    return [[Button(UNDO_LABEL, "profile_undo", turn_id)]]


def outcome_label(action: str, name: str | None = None) -> str:
    """How a pressed action button's message is redrawn."""
    if action == "profile_remove" and name is not None:
        return f"Removed: {LABELS[name]}"
    return "Undone"


def instructions_section(active: list[Entry]) -> str:
    """The saved profile, placed after the persona and platform guide."""
    if not active:
        return ""
    values = {entry.field: entry.value for entry in active}
    lines = ["# The owner's profile", "Saved from what the owner told you. Follow it."]
    # The timezone is applied to every time stamp and the context note instead.
    lines += [
        f"- {label}: {values[name]}" for name, label in _PROMPT_LABELS.items() if name in values
    ]
    lines.append(
        "The owner's latest message overrides the stored language or style for that reply."
    )
    return "\n".join(lines)


def context_lines(state: ProfileState, *, tz: str) -> list[str]:
    """Per-turn notes: removals, and what is still unknown when pacing allows a question."""
    lines: list[str] = []
    if state.removed:
        removed = ", ".join(LABELS[name] for name in state.removed)
        lines.append(
            f"The owner removed from their profile: {removed}. Do not use what they said "
            "about it in earlier messages."
        )
    if not (state.question_allowed and state.open_topics):
        return lines
    topics = ", ".join(f"{LABELS[name]} ({name})" for name in state.open_topics)
    if state.asking_now:
        lines.append(
            "The owner asked you (with /start) to get to know them now. After responding to "
            f"their latest message, ask one short, friendly question about one of: {topics}, "
            'and mark it with <pa-ask field="..."/>. If they have moved on to something '
            "else, follow them and ask nothing."
        )
    else:
        lines.append(
            f"You don't know yet: {topics}. Only at a natural moment (small talk, a greeting, "
            "or just after you finished helping) and never while the owner is in the middle "
            "of something, you may add one light, optional question about one of them after "
            'your answer, marked with <pa-ask field="..."/>. Otherwise ask nothing.'
        )
    if "timezone" in state.open_topics:
        lines.append(
            f"For the timezone, ask the owner to confirm {tz} (the local time above, by its "
            "usual name, such as WIB for Asia/Jakarta) rather than asking an open question."
        )
    return lines


def button_row(buttons: list[Button], tokens: list[str]) -> str:
    cells = "".join(
        f'<tg-button type="callback_data" data="{token}">{button.label}</tg-button>'
        for button, token in zip(buttons, tokens, strict=True)
    )
    return f"<tg-button-row>{cells}</tg-button-row>"


def open_topics(active: list[Entry], asked: dict[str, Topic]) -> list[str]:
    """Topics with no value that were neither declined nor asked twice, in order."""
    known = {entry.field for entry in active}
    open_ = []
    for name in FIELDS:
        topic = asked.get(name)
        if name in known or (topic is not None and (topic.declined or topic.asks >= 2)):
            continue
        open_.append(name)
    return open_
