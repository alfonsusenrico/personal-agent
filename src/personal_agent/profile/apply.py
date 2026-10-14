"""What a committed reply does to the profile, and the buttons code adds to it.

During the introduction, marked values become drafts and the summary is added once
no topic remains; when the model asks for the summary early, the topics left are
skipped. Otherwise marked values become pending
changes with Confirm and Cancel. The introduction offer is added once.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from psycopg import AsyncConnection

from personal_agent.conversation.models import GenerationPlan, TurnInput
from personal_agent.conversation.store import OutgoingItem
from personal_agent.profile import store
from personal_agent.profile.render import (
    Button,
    ProfileState,
    button_row,
    confirm_buttons,
    confirmation_text,
    offer_buttons,
    outcome_label,
    summary_buttons,
    summary_text,
)
from personal_agent.telegram.rich import (
    QuickReply,
    RenderedReply,
    new_button_token,
    without_quick_replies,
)


def _edit_pressed(turn: TurnInput) -> bool:
    edit = outcome_label("intro_edit")
    return any(event.kind == "button" and event.text == edit for event in turn.events)


def _started_now(turn: TurnInput) -> bool:
    """The turn begins the introduction: /start or the Start button."""
    start = outcome_label("intro_start")
    return any(
        (event.kind == "message" and (event.text or "").strip().startswith("/start"))
        or (event.kind == "button" and event.text == start)
        for event in turn.events
    )


def with_buttons(
    items: list[OutgoingItem],
    text: str | None,
    buttons: list[list[Button]],
    new_token: Callable[[], str] = new_button_token,
    *,
    only_these: bool = False,
) -> list[OutgoingItem]:
    """Add text and rows of action buttons to the last rich message, or send them alone.
    `only_these` removes the model's own quick replies from that message first."""
    last = max((index for index, item in enumerate(items) if item.kind == "rich"), default=None)
    base = items[last] if last is not None else None
    if base is not None and only_these and base.quick_replies:
        base = OutgoingItem("rich", without_quick_replies(base.text or ""), dict(base.payload))
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
    """Store the reply's profile marks and return the items with buttons added. Call
    inside the turn's commit transaction."""
    person_id = plan.person_id
    sources = [event.id for event in turn.events]
    intro = await store.introduction(connection, person_id, lock=True)
    if intro is not None and intro.state == "active":
        before = await store.entries(connection, person_id, "draft")
        remaining_before = ProfileState([], before, [], intro, []).remaining_topics()
        for mark in rendered.profile:
            if mark.value is None:
                await store.skip(connection, person_id, mark.field, now)
            else:
                await store.save_draft(
                    connection,
                    person_id,
                    mark.field,
                    mark.value,
                    source_event_ids=sources,
                    turn_id=plan.turn_id,
                    now=now,
                )
        drafts = await store.entries(connection, person_id, "draft")
        current = await store.introduction(connection, person_id)
        state = ProfileState([], drafts, [], current, [])
        if rendered.intro_summary:
            # Finishing early skips what is left, so Edit changes an answer instead of
            # resuming the questions.
            for field in state.remaining_topics():
                await store.skip(connection, person_id, field, now)
            current = await store.introduction(connection, person_id)
            state = ProfileState([], drafts, [], current, [])
        # The summary follows a change, an explicit request, or the last answer; the
        # reply to an Edit press only asks what to change.
        # Restarting a completed introduction opens with nothing left to ask.
        show = (
            rendered.intro_summary
            or bool(rendered.profile)
            or bool(remaining_before)
            or _started_now(turn)
        )
        if show and not state.remaining_topics() and not _edit_pressed(turn):
            skipped = current.skipped if current is not None else ()
            return with_buttons(
                items, summary_text(drafts, skipped), [summary_buttons(person_id)], new_token
            )
        return items
    current_values = {entry.field: entry.value for entry in turn.profile.active}
    proposed = [
        (mark.field, mark.value)
        for mark in rendered.profile
        if mark.value is not None and current_values.get(mark.field) != mark.value
    ]
    for field, value in proposed:
        await store.propose(
            connection,
            person_id,
            field,
            value,
            source_event_ids=sources,
            turn_id=plan.turn_id,
            now=now,
        )
    if proposed:
        items = with_buttons(
            items, confirmation_text(proposed), [confirm_buttons(plan.turn_id)], new_token
        )
    has_message = any(item.kind == "rich" for item in items)
    if intro is None and has_message and await store.mark_offered(connection, person_id, now):
        # A choice the model wrote itself next to the offer would look like Start but
        # start nothing.
        items = with_buttons(items, None, [offer_buttons(person_id)], new_token, only_these=True)
    return items
