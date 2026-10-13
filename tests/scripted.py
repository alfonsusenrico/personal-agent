"""Synthetic conversation scripts (tests/conversations/*.yaml).

A script lists what the owner does (messages, edits, media, forwards, waits, a
message sent while the assistant is thinking) and the structural outcome expected.
The same scripts run free in the test suite against a fake gateway and, through
`scripts/converse`, against the real gateway for a transcript the owner reviews.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from llm_gateway_client import CallResult
from psycopg_pool import AsyncConnectionPool

from personal_agent.gateway.calls import Responses
from tests.fakes import FakeTelegramServer, message_update
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
    }
)


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

    def __init__(self, inner: Responses) -> None:
        self._inner = inner
        self.calls: list[RecordedCall] = []
        self.on_next_call: list[dict[str, Any]] = []
        self.agent: Agent[RecordingResponses] | None = None

    async def create(self, **kwargs: Any) -> CallResult:
        recorded = RecordedCall(body=kwargs)
        self.calls.append(recorded)
        if self.on_next_call and self.agent is not None:
            updates, self.on_next_call = self.on_next_call, []
            await self.agent.receive(*updates)
        try:
            recorded.result = await self._inner.create(**kwargs)
        except Exception as exc:
            recorded.error = getattr(exc, "code", type(exc).__name__)
            raise
        return recorded.result


@dataclass
class ScriptRun:
    name: str
    description: str
    owner_lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)


def load_script(path: Path) -> dict[str, Any]:
    script = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list):
        raise ValueError(f"{path.name}: a script needs a steps list")
    unknown = set(script.get("expect") or {}) - _EXPECTATION_KEYS
    if unknown:
        raise ValueError(f"{path.name}: unknown expectations {sorted(unknown)}")
    return script


def script_paths() -> list[Path]:
    return sorted(SCRIPTS_DIR.glob("*.yaml"))


class _Updates:
    """Builds Telegram updates for script steps, keeping message references."""

    def __init__(self, agent: Agent[RecordingResponses]) -> None:
        self._agent = agent
        self._next_id = 1
        self._messages: dict[str, tuple[int, Any]] = {}

    def build(self, step: dict[str, Any]) -> tuple[dict[str, Any], str]:
        update_id = self._next_id
        self._next_id += 1
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
    script: dict[str, Any], pool: AsyncConnectionPool, agent: Agent[RecordingResponses]
) -> ScriptRun:
    run = ScriptRun(name=script["name"], description=script.get("description", ""))
    agent.responses.agent = agent
    updates = _Updates(agent)
    for step in script["steps"]:
        if "wait" in step:
            agent.clock.advance(float(step["wait"]))
        elif step.get("settle"):
            await agent.settle()
        elif "while_thinking" in step:
            update, label = updates.build(step["while_thinking"])
            agent.responses.on_next_call.append(update)
            run.owner_lines.append(f"{label}  (while the assistant is thinking)")
        else:
            update, label = updates.build(step)
            await agent.receive(update)
            run.owner_lines.append(label)
    await agent.settle()
    run.calls = agent.responses.calls
    run.replies = list(agent.server.sent_texts)
    run.model_calls = await _model_calls(pool)
    run.failures = await _check(script.get("expect") or {}, run, pool)
    return run


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
) -> Agent[RecordingResponses]:
    return Agent(
        pool,
        FakeTelegramServer(),
        RecordingResponses(inner),
        clock,
        profile=profile,
        reasoning_effort=reasoning_effort,
    )
