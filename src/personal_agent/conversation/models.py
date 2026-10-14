"""Records the conversation core passes between the store, context building, and calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from personal_agent.profile.render import ProfileState

Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class EventRow:
    id: int
    kind: Literal["message", "edit", "unsupported", "button", "poll_answer", "stop", "command"]
    modality: str
    occurred_at: datetime
    trust: Literal["owner_direct", "owner_forwarded"]
    forward_origin: dict[str, Any] | None
    text: str | None
    external_chat_id: str
    external_message_id: str | None
    edit_of: int | None
    # For an edit: whether the edited message belongs to the same turn, and the
    # text it had just before this edit.
    edits_same_turn: bool = False
    previous_text: str | None = None


@dataclass(frozen=True, slots=True)
class MessageRow:
    id: int
    role: Role
    text: str
    occurred_at: datetime
    person_tz: str


@dataclass(frozen=True, slots=True)
class GenerationPlan:
    """The frozen inputs of one generation; rebuilding from them gives an identical call."""

    turn_id: int
    # The turn's random key that names its model calls.
    call_key: str
    person_id: int
    conversation_id: int
    generation: int
    started_at: datetime
    through_event_id: int
    # The Telegram draft of this generation, so a Stop press finds its turn.
    draft_id: int = 1
    stop_requested: bool = False
    # A command answered by code without a model call, such as "profile".
    command: str | None = None


@dataclass(frozen=True, slots=True)
class TurnInput:
    plan: GenerationPlan
    person_tz: str
    chat_id: str
    events: list[EventRow]
    # Earlier messages, oldest first.
    window: list[MessageRow]
    profile: ProfileState = field(default_factory=ProfileState)
