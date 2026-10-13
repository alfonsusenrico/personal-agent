"""One worker's poller, runner, and sender, stepped by hand against a fake Telegram
server and the real database. Shared by the tests and `scripts/converse`."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from psycopg_pool import AsyncConnectionPool

from personal_agent.conversation.runner import RunnerConfig, TurnRunner
from personal_agent.gateway.calls import Responses
from personal_agent.telegram.poller import Poller
from personal_agent.telegram.sender import Sender
from tests.fakes import OWNER_ID, FakeClock, FakeTelegramServer, RecordingDrafts

TIMEZONE = "Asia/Jakarta"


class Agent[R: Responses]:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        server: FakeTelegramServer,
        responses: R,
        clock: FakeClock,
        *,
        gateway: Responses | None = None,
        profile: str = "reply",
        reasoning_effort: str | None = None,
    ) -> None:
        self.server = server
        self.responses = responses
        self.clock = clock
        self.sleeps: list[float] = []
        self.drafts = RecordingDrafts()
        api = server.api()
        self.runner = TurnRunner(
            pool,
            gateway or responses,
            RunnerConfig(
                reply_profile=profile,
                default_timezone=TIMEZONE,
                debounce=timedelta(seconds=2),
                window_budget_tokens=6_000,
                offline_after=timedelta(seconds=300),
                reasoning_effort=reasoning_effort,
            ),
            clock=clock,
            drafts=self.drafts,
            on_answer=lambda: None,
        )
        self.poller = Poller(
            pool,
            api,
            allowed_user_ids=frozenset({OWNER_ID}),
            default_timezone=TIMEZONE,
            clock=clock,
            on_stored=lambda: None,
            on_stop=self.runner.request_stop,
        )
        self.sender = Sender(pool, api, clock=clock, sleep=self._sleep)

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.clock.advance(seconds)

    async def receive(self, *updates: dict[str, Any]) -> None:
        self.server.updates.extend(updates)
        await self.poller.poll_once()

    async def think(self) -> None:
        """Run turns until none is open, letting debounce time pass."""
        for _ in range(50):
            delay = await self.runner.step()
            if delay is None:
                return
            if delay > 0:
                self.clock.advance(delay)
        raise AssertionError("turns did not settle")

    async def settle(self) -> None:
        await self.think()
        if not await self.sender.send_pending():
            raise AssertionError("the outbox did not drain")
