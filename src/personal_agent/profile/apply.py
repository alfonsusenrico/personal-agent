"""What a committed reply does to the profile, and the Undo line code adds to it.

Values the reply marked are saved at once and listed under it with one Undo
button; declined topics and getting-to-know questions are recorded for pacing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from psycopg import AsyncConnection

from personal_agent.conversation.models import GenerationPlan, TurnInput
from personal_agent.conversation.store import OutgoingItem
from personal_agent.profile import store
from personal_agent.profile.render import Button, button_row, saved_text, undo_button
from personal_agent.telegram.rich import QuickReply, RenderedReply, new_button_token


def with_buttons(
    items: list[OutgoingItem],
    text: str | None,
    buttons: list[list[Button]],
    new_token: Callable[[], str] = new_button_token,
) -> list[OutgoingItem]:
    """Add text and rows of action buttons to the last rich message, or send them alone."""
    last = max((index for index, item in enumerate(items) if item.kind == "rich"), default=None)
    base = items[last] if last is not None else None
    replies = list(base.quick_replies) if base is not None else []
    rows = []
    for row in buttons:
        tokens = [new_token() for _ in row]
        rows.append(button_row(row, tokens))
        for button, token in zip(row, tokens, strict=True):
            replies.append(
                QuickReply(token, button.label, len(replies), button.action, button.target_id)
            )
    parts = [base.text] if base is not None and base.text else []
    if text:
        parts.append(text)
    markdown = "\n\n".join(parts) + "\n" + "\n".join(rows)
    payload = dict(base.payload) if base is not None else {}
    updated = OutgoingItem("rich", markdown, payload, quick_replies=replies)
    if last is None:
        return [*items, updated]
    return [*items[:last], updated, *items[last + 1 :]]


async def apply_reply(
    connection: AsyncConnection,
    plan: GenerationPlan,
    turn: TurnInput,
    rendered: RenderedReply,
    items: list[OutgoingItem],
    *,
    now: datetime,
    new_token: Callable[[], str] = new_button_token,
) -> list[OutgoingItem]:
    """Save the reply's profile marks and return the items with the Undo line added.
    Call inside the turn's commit transaction."""
    person_id = plan.person_id
    sources = [event.id for event in turn.events]
    saved: list[tuple[str, str]] = []
    for mark in rendered.profile:
        if mark.value is None:
            await store.record_decline(connection, person_id, mark.field, now)
            continue
        changed = await store.save(
            connection,
            person_id,
            mark.field,
            mark.value,
            source_event_ids=sources,
            turn_id=plan.turn_id,
            now=now,
        )
        if changed:
            saved.append((mark.field, mark.value))
            if mark.field == "assistant_name":
                await store.queue_bot_name(connection, person_id, mark.value, now)
    for name in rendered.asks:
        await store.record_ask(connection, person_id, name, now)
    # /start asks until a reply asks nothing: the owner moved on or nothing is open.
    if not rendered.asks and await store.asking_now(connection, person_id):
        await store.set_asking_now(connection, person_id, None)
    if saved:
        items = with_buttons(items, saved_text(saved), undo_button(plan.turn_id), new_token)
    return items
