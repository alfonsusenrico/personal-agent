"""Synthetic conversation scripts (tests/conversations/*.yaml).

A script lists what the owner does (messages, edits, media, forwards, waits, a
message sent while the assistant is thinking) and the structural outcome expected.
The same scripts run free in the test suite against a fake gateway and, through
`scripts/converse`, against the real gateway for a transcript the owner reviews.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from llm_gateway_client import CallResult, Completed, StreamEvent
from psycopg_pool import AsyncConnectionPool

from personal_agent.gateway.calls import Responses
from tests.fakes import (
    FakeTelegramServer,
    button_press,
    message_update,
    poll_vote,
    stop_press,
)
from tests.harness import Agent

SCRIPTS_DIR = Path(__file__).with_name("conversations")
_EXPECTATION_KEYS = frozenset(
    {
        "turns",
        "model_calls",
        "superseded_calls",
        "merged_sources",
        "context_contains",
        "context_excludes",
        "user_contains",
        "user_excludes",
        "turn_statuses",
    }
)
# Checked only in the free run, where the replies come from `fake_replies`.
_FAKE_EXPECTATION_KEYS = frozenset({"sent_methods", "sent_contains", "sent_excludes"})
UpdateFactory = Callable[[], dict[str, Any] | None]


@dataclass
class RecordedCall:
    body: dict[str, Any]
    result: CallResult | None = None
    error: str | None = None

    @property
    def developer_note(self) -> str:
        return next(item["content"] for item in self.body["input"] if item["role"] == "developer")

    @property
    def user_content(self) -> str:
        return self.body["input"][-1]["content"]


class RecordingResponses:
    """Records every call and delivers messages the script sends mid-thought."""

    def __init__(self, inner: Responses, *, stub_replies: list[str] | None = None) -> None:
        self._inner = inner
        # Scripted answers for the gateway's stub model, one per call, in order.
        self._stub_replies = stub_replies
        self.calls: list[RecordedCall] = []
        # Built when the call starts, so they can refer to its draft.
        self.on_next_call: list[UpdateFactory] = []
        self.agent: Agent[RecordingResponses] | None = None

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        if self._stub_replies:
            kwargs = {**kwargs, "stub": {"reply": self._stub_replies.pop(0)}}
        recorded = RecordedCall(body=kwargs)
        self.calls.append(recorded)
        if self.on_next_call and self.agent is not None:
            factories, self.on_next_call = self.on_next_call, []
            updates = [update for update in (factory() for factory in factories) if update]
            await self.agent.receive(*updates)
        try:
            async for event in self._inner.stream(**kwargs):
                if isinstance(event, Completed):
                    recorded.result = event.result
                yield event
        except Exception as exc:
            recorded.error = getattr(exc, "code", type(exc).__name__)
            raise


@dataclass
class ScriptRun:
    name: str
    description: str
    owner_lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    sent_methods: list[str] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)


def load_script(path: Path) -> dict[str, Any]:
    script = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list):
        raise ValueError(f"{path.name}: a script needs a steps list")
    unknown = set(script.get("expect") or {}) - _EXPECTATION_KEYS
    unknown |= set(script.get("expect_fake") or {}) - _FAKE_EXPECTATION_KEYS
    if unknown:
        raise ValueError(f"{path.name}: unknown expectations {sorted(unknown)}")
    if not all(isinstance(reply, str) for reply in script.get("fake_replies") or []):
        raise ValueError(f"{path.name}: fake_replies must be strings")
    return script


def describe_sent(item: dict[str, Any]) -> str:
    """One line per thing sent to Telegram, for transcripts."""
    method = item["method"]
    if method == "sendRichMessage":
        effect = " [with an effect]" if "message_effect_id" in item else ""
        return f"{item['rich_message']['markdown']}{effect}"
    if method == "sendMessage":
        return f"{item['text']} [plain]"
    if method == "setMessageReaction":
        return f"[reacted {item['reaction'][0]['emoji']}]"
    if method == "sendPoll":
        options = " / ".join(option["text"] for option in item["options"])
        return f"[poll: {item['question']} — {options}]"
    return f"[{method}]"


def script_paths() -> list[Path]:
    return sorted(SCRIPTS_DIR.glob("*.yaml"))


class _Updates:
    """Builds Telegram updates for script steps, keeping message references."""

    def __init__(self, agent: Agent[RecordingResponses], pool: AsyncConnectionPool) -> None:
        self._agent = agent
        self._pool = pool
        self._next_id = 1
        self._messages: dict[str, tuple[int, Any]] = {}

    def _take_id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    async def press(self, label: str) -> dict[str, Any] | None:
        """The latest unused quick reply whose label contains `label`, as a press."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT b.token, o.external_message_id FROM reply_button b
                JOIN outbox o ON o.id = b.outbox_id
                WHERE b.label ILIKE %s AND b.pressed_at IS NULL AND o.external_message_id IS NOT NULL
                ORDER BY b.id DESC LIMIT 1
                """,
                (f"%{label}%",),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return button_press(self._take_id(), row[0], message_id=int(row[1]))

    async def vote(self, labels: list[str]) -> dict[str, Any] | None:
        """A vote for the options of the latest poll whose text contains each label."""
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT external_poll_id, options FROM reply_poll "
                "WHERE external_poll_id IS NOT NULL ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        poll_id, options = row
        chosen = [
            index
            for index, option in enumerate(options)
            if any(label.lower() in option.lower() for label in labels)
        ]
        return poll_vote(self._take_id(), poll_id, chosen) if chosen else None

    def stop(self) -> dict[str, Any] | None:
        """A Stop press on the draft being shown."""
        if not self._agent.drafts.opened:
            return None
        return stop_press(self._take_id(), self._agent.drafts.opened[-1][1])

    def build(self, step: dict[str, Any]) -> tuple[dict[str, Any], str]:
        update_id = self._take_id()
        now = self._agent.clock.now
        sent_at = now - timedelta(minutes=float(step.get("minutes_ago", 0)))
        if "edit" in step:
            message_id, original_date = self._messages[step["edit"]["ref"]]
            text = step["edit"]["text"]
            update = message_update(
                update_id, text, message_id=message_id, date=original_date, edit_date=now
            )
            return update, f"(edits {step['edit']['ref']}) {text}"
        if "send" in step:
            update = message_update(update_id, step["send"], date=sent_at)
            label = step["send"]
        elif "media" in step:
            media = step["media"]
            kind = media["kind"]
            extra: dict[str, Any] = {
                kind: [{"file_id": "f"}] if kind == "photo" else {"file_id": "f"}
            }
            if media.get("caption"):
                extra["caption"] = media["caption"]
            update = message_update(update_id, date=sent_at, extra=extra)
            label = f"[{kind}] {media.get('caption', '')}".strip()
        elif "forward" in step:
            forward = step["forward"]
            origin = {
                "type": "user",
                "date": int(sent_at.timestamp()),
                "sender_user": {"id": 77, "is_bot": False, "first_name": forward["from"]},
            }
            update = message_update(
                update_id, forward["text"], date=sent_at, extra={"forward_origin": origin}
            )
            label = f"[forwarded from {forward['from']}] {forward['text']}"
        else:
            raise ValueError(f"unknown step {sorted(step)}")
        if "ref" in step:
            self._messages[step["ref"]] = (update_id, sent_at)
        if step.get("minutes_ago"):
            label = f"{label}  (sent {step['minutes_ago']} min earlier)"
        return update, label


async def run_script(
    script: dict[str, Any],
    pool: AsyncConnectionPool,
    agent: Agent[RecordingResponses],
    *,
    fake: bool = False,
) -> ScriptRun:
    """Play a script. `fake` checks the reply-side expectations too, because the
    replies then come from the script's `fake_replies`."""
    run = ScriptRun(name=script["name"], description=script.get("description", ""))
    agent.responses.agent = agent
    updates = _Updates(agent, pool)
    for step in script["steps"]:
        if "wait" in step:
            agent.clock.advance(float(step["wait"]))
        elif step.get("settle"):
            await agent.settle()
        elif "while_thinking" in step:
            update, label = updates.build(step["while_thinking"])
            agent.responses.on_next_call.append(lambda update=update: update)
            run.owner_lines.append(f"{label}  (while the assistant is thinking)")
        elif step.get("stop_while_thinking"):
            agent.responses.on_next_call.append(updates.stop)
            run.owner_lines.append("[pressed Stop while the reply was streaming]")
        elif "press" in step:
            await agent.settle()
            press = await updates.press(step["press"])
            if press is None:
                run.failures.append(f"no quick reply matching {step['press']!r} to press")
                continue
            await agent.receive(press)
            run.owner_lines.append(f"[pressed the button {step['press']!r}]")
        elif "vote" in step:
            await agent.settle()
            vote = await updates.vote(step["vote"])
            if vote is None:
                run.failures.append(f"no poll option matching {step['vote']!r} to vote for")
                continue
            await agent.receive(vote)
            run.owner_lines.append(f"[voted {', '.join(step['vote'])}]")
        else:
            update, label = updates.build(step)
            await agent.receive(update)
            run.owner_lines.append(label)
    await agent.settle()
    run.calls = agent.responses.calls
    run.replies = [describe_sent(item) for item in agent.server.sent]
    run.sent_methods = [item["method"] for item in agent.server.sent]
    run.model_calls = await _model_calls(pool)
    run.failures += await _check(script.get("expect") or {}, run, pool)
    if fake:
        run.failures += _check_sent(script.get("expect_fake") or {}, run)
    return run


def _check_sent(expect: dict[str, Any], run: ScriptRun) -> list[str]:
    failures: list[str] = []
    if "sent_methods" in expect and expect["sent_methods"] != run.sent_methods:
        failures.append(
            f"sent_methods: expected {expect['sent_methods']!r}, got {run.sent_methods!r}"
        )
    sent = "\n".join(run.replies)
    failures += [
        f"sent lacks {text!r}" for text in expect.get("sent_contains", []) if text not in sent
    ]
    failures += [f"sent has {text!r}" for text in expect.get("sent_excludes", []) if text in sent]
    return failures


async def _model_calls(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as connection:
        cursor = await connection.execute(
            """
            SELECT call_id, status, input_tokens, cached_input_tokens, output_tokens,
                   reasoning_tokens, cost_usd, latency_ms
            FROM model_call ORDER BY id
            """
        )
        assert cursor.description is not None
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in await cursor.fetchall()]


async def _check(expect: dict[str, Any], run: ScriptRun, pool: AsyncConnectionPool) -> list[str]:
    failures: list[str] = []
    async with pool.connection() as connection:
        cursor = await connection.execute("SELECT count(*) FROM turn")
        turns = (await cursor.fetchone() or (0,))[0]
        cursor = await connection.execute(
            "SELECT cardinality(source_event_ids) FROM message WHERE role = 'user' ORDER BY id"
        )
        merged = [row[0] for row in await cursor.fetchall()]
        cursor = await connection.execute("SELECT status FROM turn ORDER BY id")
        statuses = [row[0] for row in await cursor.fetchall()]
    superseded = sum(1 for call in run.model_calls if call["status"] == "superseded")
    notes = "\n".join(call.developer_note for call in run.calls)
    users = "\n".join(call.user_content for call in run.calls)

    def compare(key: str, actual: object) -> None:
        if key in expect and expect[key] != actual:
            failures.append(f"{key}: expected {expect[key]!r}, got {actual!r}")

    compare("turns", turns)
    compare("model_calls", len(run.model_calls))
    compare("superseded_calls", superseded)
    compare("merged_sources", merged)
    compare("turn_statuses", statuses)
    for text in expect.get("context_contains", []):
        if text not in notes:
            failures.append(f"context note lacks {text!r}")
    for text in expect.get("context_excludes", []):
        if text in notes:
            failures.append(f"context note has {text!r}")
    for text in expect.get("user_contains", []):
        if text not in users:
            failures.append(f"owner message lacks {text!r}")
    for text in expect.get("user_excludes", []):
        if text in users:
            failures.append(f"owner message has {text!r}")
    return failures


def new_agent(
    pool: AsyncConnectionPool,
    inner: Responses,
    clock: Any,
    *,
    profile: str = "reply",
    reasoning_effort: str | None = None,
    stub_replies: list[str] | None = None,
) -> Agent[RecordingResponses]:
    return Agent(
        pool,
        FakeTelegramServer(),
        RecordingResponses(inner, stub_replies=stub_replies),
        clock,
        profile=profile,
        reasoning_effort=reasoning_effort,
    )
