"""The worker's health endpoint and the polling status it reports."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import httpx2
import pytest
from psycopg_pool import AsyncConnectionPool

from personal_agent.health import STALL_SECONDS, HealthEndpoint
from personal_agent.telegram.poller import Poller, PollingStatus
from tests.fakes import START, FakeTelegramServer

pytestmark = pytest.mark.anyio
REVISION = "0123456789abcdef0123456789abcdef01234567"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def endpoint(status: PollingStatus, clock: Clock, *, database: bool = True) -> HealthEndpoint:
    async def ready() -> bool:
        return database

    return HealthEndpoint(revision=REVISION, polling=status, database_ready=ready, monotonic=clock)


async def test_healthy_reports_the_revision_and_nothing_else() -> None:
    clock = Clock()
    status = PollingStatus(last_success=clock.now - 20)
    code, body = await endpoint(status, clock).report()
    assert code == 200
    assert body == {"status": "ok", "revision": REVISION, "polling": True, "database": True}


@pytest.mark.parametrize(
    "status,database,polling",
    [
        (PollingStatus(), True, False),
        (PollingStatus(last_success=1_000.0 - STALL_SECONDS - 1), True, False),
        (PollingStatus(last_success=1_000.0, conflict=True), True, False),
        (PollingStatus(last_success=1_000.0), False, True),
    ],
    ids=["before the first poll", "polling stalled", "another consumer", "database down"],
)
async def test_unhealthy_states(status: PollingStatus, database: bool, polling: bool) -> None:
    code, body = await endpoint(status, Clock(), database=database).report()
    assert code == 503
    assert body == {
        "status": "unhealthy",
        "revision": REVISION,
        "polling": polling,
        "database": database,
    }


async def test_the_endpoint_over_http() -> None:
    clock = Clock()
    status = PollingStatus(last_success=clock.now)
    server = await endpoint(status, clock).serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            healthy = await http.get("/health")
            status.conflict = True
            unhealthy = await http.get("/health")
            elsewhere = await http.get("/")
            posted = await http.post("/health")
    finally:
        server.close()
        await server.wait_closed()
    assert healthy.status_code == 200 and healthy.json()["revision"] == REVISION
    assert healthy.headers["cache-control"] == "no-store"
    assert unhealthy.status_code == 503 and unhealthy.json()["polling"] is False
    assert (elsewhere.status_code, posted.status_code) == (404, 405)


async def test_a_request_without_headers_is_answered() -> None:
    clock = Clock()
    server = await endpoint(PollingStatus(), clock).serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /health HTTP/1.1\r\n")
        await writer.drain()
        # The request ends before its blank line; the server answers what it has.
        writer.write_eof()
        answer = await asyncio.wait_for(reader.read(), timeout=10)
        writer.close()
    finally:
        server.close()
        await server.wait_closed()
    assert answer.startswith(b"HTTP/1.1 503")
    assert json.loads(answer.split(b"\r\n\r\n", 1)[1])["status"] == "unhealthy"


class StopPolling(Exception):
    pass


async def test_the_poller_records_success_and_a_conflict() -> None:
    conflict = httpx2.Response(409, json={"ok": False, "error_code": 409})
    no_updates = httpx2.Response(200, json={"ok": True, "result": []})
    telegram = FakeTelegramServer()
    telegram.failures["getUpdates"] = [conflict, no_updates, conflict]
    clock = Clock()
    seen: list[PollingStatus] = []

    async def sleep(_seconds: float) -> None:
        seen.append(PollingStatus(poller.status.last_success, poller.status.conflict))
        clock.now += 5
        if len(seen) == 2:
            raise StopPolling

    poller = Poller(
        cast(AsyncConnectionPool, None),  # no updates arrive, so nothing is stored
        telegram.api(),
        allowed_user_ids=frozenset({1001}),
        default_timezone="Asia/Jakarta",
        clock=lambda: START,
        on_stored=lambda: None,
        sleep=sleep,
        long_poll_seconds=0,
        monotonic=clock,
    )
    with pytest.raises(StopPolling):
        await poller.run_forever()
    # A conflict before any success; the success in between records its time and clears
    # the conflict, and the next conflict is recorded again.
    assert seen == [PollingStatus(None, True), PollingStatus(1_005.0, True)]
    assert len(telegram.requests) == 3
