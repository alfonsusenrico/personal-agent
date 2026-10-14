"""Buttons handled by code: the press runs in the same transaction that stores it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection

from personal_agent.profile import store
from personal_agent.profile.render import outcome_label
from personal_agent.profile.store import Entry


@dataclass(frozen=True, slots=True)
class ActionResult:
    # False when the button's target already changed; nothing was done.
    done: bool
    # True when the model answers the press (Start and Edit ask the next question).
    answerable: bool = False
    # The redrawn message's outcome line.
    label: str | None = None


_STALE = ActionResult(done=False)


async def _rename_if_changed(
    connection: AsyncConnection, person_id: int, changed: list[Entry], now: datetime
) -> None:
    for entry in changed:
        if entry.field == "assistant_name":
            await store.queue_bot_name(connection, person_id, entry.value, now)


async def press(
    connection: AsyncConnection, person_id: int, action: str, target_id: int, now: datetime
) -> ActionResult:
    """Run an action button once. Call inside the transaction that stores the press."""
    if action == "intro_start":
        await store.start_introduction(connection, person_id, now)
        return ActionResult(True, answerable=True, label=outcome_label(action))
    if action == "intro_later":
        if not await store.decline_introduction(connection, person_id, now):
            return _STALE
        return ActionResult(True, label=outcome_label(action))
    if action == "intro_edit":
        intro = await store.introduction(connection, person_id, lock=True)
        if intro is None or intro.state != "active":
            return _STALE
        return ActionResult(True, answerable=True, label=outcome_label(action))
    if action == "intro_save":
        changed = await store.save_introduction(connection, person_id, now)
        if changed is None:
            return _STALE
        await _rename_if_changed(connection, person_id, changed, now)
        # The model closes the introduction in one sentence, under the new names.
        return ActionResult(True, answerable=True, label=outcome_label(action))
    if action == "profile_confirm":
        changed = await store.confirm_pending(connection, person_id, target_id, now)
        if changed is None:
            return _STALE
        await _rename_if_changed(connection, person_id, changed, now)
        return ActionResult(True, label=outcome_label(action))
    if action == "profile_cancel":
        if not await store.cancel_pending(connection, person_id, target_id):
            return _STALE
        return ActionResult(True, label=outcome_label(action))
    if action == "profile_remove":
        field = await store.remove(connection, person_id, target_id, now)
        if field is None:
            return _STALE
        return ActionResult(True, label=outcome_label(action, field))
    raise ValueError(f"unknown action {action}")
