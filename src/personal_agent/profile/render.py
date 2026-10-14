"""Text the profile adds to replies and prompts: rendered by code from stored rows,
so the owner always sees and confirms exactly what is stored."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from personal_agent.profile.fields import FIELDS, LABELS
from personal_agent.profile.store import Entry, Introduction

# Stored as the assistant's message for /profile, so profile values do not linger in
# the recent window after a removal.
PROFILE_SHOWN = "[the owner's profile was shown]"
EMPTY_PROFILE = "Your profile is empty. Send /start for a short introduction."
CHANGE_HINT = "To change something, just tell me."
# How each confirmed value is named in the model's instructions.
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
    primary: bool = False


@dataclass(frozen=True, slots=True)
class ProfileState:
    """What the reply call and its commit need to know about the profile."""

    active: list[Entry]
    drafts: list[Entry]
    pending: list[Entry]
    intro: Introduction | None
    # Fields removed since the oldest message in the recent window.
    removed: list[str]

    @property
    def intro_active(self) -> bool:
        return self.intro is not None and self.intro.state == "active"

    def remaining_topics(self) -> list[str]:
        """Introduction topics with neither a draft nor a skip, in order."""
        if self.intro is None:
            return list(FIELDS)
        answered = {entry.field for entry in self.drafts} | set(self.intro.skipped)
        return [field for field in FIELDS if field not in answered]


def _value_lines(entries: list[Entry]) -> list[str]:
    return [f"**{LABELS[entry.field]}:** {entry.value}" for entry in entries]


def summary_text(drafts: list[Entry], skipped: tuple[str, ...]) -> str:
    lines = _value_lines(drafts) or ["Nothing to save yet."]
    if skipped:
        names = ", ".join(LABELS[field] for field in FIELDS if field in skipped)
        lines.append(f"Skipped: {names}")
    return "\n".join(lines)


def confirmation_text(proposed: list[tuple[str, str]]) -> str:
    """The changes a reply proposes, as (field, value) pairs."""
    return "\n".join(f"**{LABELS[field]}** → {value}" for field, value in proposed)


def profile_text(active: list[Entry], tz: str) -> str:
    if not active:
        return EMPTY_PROFILE
    zone = ZoneInfo(tz)
    lines = ["**Your profile**"]
    for entry in active:
        confirmed = entry.confirmed_at
        date = f" · {confirmed.astimezone(zone):%-d %b %Y}" if confirmed else ""
        lines.append(f"**{LABELS[entry.field]}:** {entry.value}{date}")
    lines.append(CHANGE_HINT)
    return "\n".join(lines)


def remove_buttons(active: list[Entry]) -> list[list[Button]]:
    return [
        [Button(f"Remove: {LABELS[entry.field]}", "profile_remove", entry.id)] for entry in active
    ]


def instructions_section(active: list[Entry]) -> str:
    """The confirmed profile, placed after the persona and platform guide."""
    if not active:
        return ""
    values = {entry.field: entry.value for entry in active}
    lines = ["# The owner's profile", "Confirmed by the owner. Follow it without asking about it."]
    # The timezone is applied to every time stamp and the context note instead.
    lines += [
        f"- {label}: {values[field]}" for field, label in _PROMPT_LABELS.items() if field in values
    ]
    lines.append(
        "The owner's latest message overrides the stored language or style for that reply."
    )
    return "\n".join(lines)


def _listing(entries: list[Entry]) -> str:
    return "; ".join(f"{LABELS[entry.field]}: {entry.value}" for entry in entries)


def context_lines(state: ProfileState, *, offer_due: bool, tz: str) -> list[str]:
    """Per-turn notes about the introduction, waiting changes, and removals."""
    lines: list[str] = []
    if offer_due:
        lines.append(
            "After answering, add one short sentence offering a quick introduction, so you "
            "can learn what to call the owner, your own name, and how they like replies. "
            "Start and Later buttons are added for you, so write no buttons of your own."
        )
    if state.intro_active and state.intro is not None:
        remaining = state.remaining_topics()
        lines.append("The introduction is in progress.")
        if state.drafts:
            lines.append(f"Answered so far: {_listing(state.drafts)}.")
        if state.intro.skipped:
            skipped = ", ".join(LABELS[field] for field in FIELDS if field in state.intro.skipped)
            lines.append(f"Skipped: {skipped}.")
        if remaining:
            topics = ", ".join(f"{LABELS[field]} ({field})" for field in remaining)
            lines.append(f"Topics still open, in order: {topics}.")
            lines.append(
                "If the owner's latest message answers the first open topic, mark it with "
                '<pa-profile field="...">value</pa-profile> (or <pa-profile field="..." '
                'skip="true"/> if they skip it) and ask about the next one; otherwise ask '
                "about the first open topic. Ask one short question at a time. Acknowledge "
                "an answer in a few words at most and do not repeat it back: the summary at "
                "the end shows everything. When the owner wants to finish early, add "
                "<pa-intro-summary/>; the summary with Save and Edit buttons is added for you."
            )
            if "timezone" in remaining[:2]:
                lines.append(
                    f"For the timezone, ask the owner to confirm {tz} (the local time above) "
                    "or name the place they are in; mark it as an IANA name."
                )
        else:
            lines.append(
                "Every topic is answered. If the owner changes one, mark the new value. The "
                "summary with Save and Edit buttons is added after a change, or when you add "
                "<pa-intro-summary/> (do so when the owner has nothing more to change), so "
                "write only a short lead-in to it."
            )
    if state.pending:
        lines.append(f"Waiting for the owner to confirm: {_listing(state.pending)}.")
    if state.removed:
        removed = ", ".join(LABELS[field] for field in state.removed)
        lines.append(
            f"The owner removed from their profile: {removed}. Do not use what they said "
            "about it in earlier messages."
        )
    return lines


def button_row(buttons: list[Button], tokens: list[str]) -> str:
    cells = []
    for button, token in zip(buttons, tokens, strict=True):
        style = ' style="primary"' if button.primary else ""
        cells.append(
            f'<tg-button type="callback_data" data="{token}"{style}>{button.label}</tg-button>'
        )
    return f"<tg-button-row>{''.join(cells)}</tg-button-row>"


def outcome_label(action: str, field: str | None = None) -> str:
    """How a pressed action button's message is redrawn."""
    if action == "profile_remove" and field is not None:
        return f"Removed: {LABELS[field]}"
    return {
        "intro_start": "Start introduction",
        "intro_later": "Later",
        "intro_save": "Saved",
        "intro_edit": "Edit",
        "profile_confirm": "Saved",
        "profile_cancel": "Cancelled",
    }.get(action, "Done")


def offer_buttons(person_id: int) -> list[Button]:
    return [
        Button("Start introduction", "intro_start", person_id, primary=True),
        Button("Later", "intro_later", person_id),
    ]


def summary_buttons(person_id: int) -> list[Button]:
    return [
        Button("Save", "intro_save", person_id, primary=True),
        Button("Edit", "intro_edit", person_id),
    ]


def confirm_buttons(turn_id: int) -> list[Button]:
    return [
        Button("Confirm", "profile_confirm", turn_id, primary=True),
        Button("Cancel", "profile_cancel", turn_id),
    ]
