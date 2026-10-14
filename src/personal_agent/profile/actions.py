"""Buttons handled by code: the press runs in the same transaction that stores it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection

from personal_agent.profile import store
from personal_agent.profile.render import outcome_label


@dataclass(frozen=True, slots=True)
class ActionResult:
    # False when the button's target already changed; nothing was done.
    done: bool
    # The redrawn message's outcome line.
    label: str | None = None


_STALE = ActionResult(done=False)


async def press(
    connection: AsyncConnection, person_id: int, action: str, target_id: int, now: datetime
) -> ActionResult:
    """Run an action button once. Call inside the transaction that stores the press."""
    if action == "profile_undo":
        restored = await store.undo_turn(connection, person_id, target_id)
        if restored is None:
            return _STALE
        for name, value in restored:
            if name == "assistant_name" and value is not None:
                await store.queue_bot_name(connection, person_id, value, now)
        return ActionResult(True, outcome_label(action))
    if action == "profile_remove":
        removed = await store.remove(connection, person_id, target_id, now)
        if removed is None:
            return _STALE
        return ActionResult(True, outcome_label(action, removed))
    raise ValueError(f"unknown action {action}")
