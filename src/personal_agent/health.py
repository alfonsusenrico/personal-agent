"""The worker's health endpoint for the release engine and the server's monitor.

`GET /health` answers 200 when polling stored its updates within the last 90 seconds, no
other consumer holds the bot, and the database answers; 503 otherwise. The body carries
the baked revision and two booleans, never content or identifiers. Compose publishes the
port on loopback only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from personal_agent.telegram.poller import PollingStatus

# Long polls end every 30 seconds, so three missed polls mean polling has stalled.
STALL_SECONDS = 90.0
_REQUEST_TIMEOUT_SECONDS = 5.0
_MAX_HEADER_LINES = 50
logger = logging.getLogger(__name__)


class HealthEndpoint:
    def __init__(
        self,
        *,
        revision: str,
        polling: PollingStatus,
        database_ready: Callable[[], Awaitable[bool]],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._revision = revision
        self._polling = polling
        self._database_ready = database_ready
        self._monotonic = monotonic

    def polling_ok(self) -> bool:
        last = self._polling.last_success
        if last is None or self._polling.conflict:
            return False
        return self._monotonic() - last <= STALL_SECONDS

    async def report(self) -> tuple[int, dict[str, object]]:
        polling = self.polling_ok()
        database = await self._database_ready()
        healthy = polling and database
        body: dict[str, object] = {
            "status": "ok" if healthy else "unhealthy",
            "revision": self._revision,
            "polling": polling,
            "database": database,
        }
        return (200 if healthy else 503), body

    async def _respond(self, reader: asyncio.StreamReader) -> tuple[int, dict[str, object]]:
        request_line = await reader.readline()
        for _ in range(_MAX_HEADER_LINES):
            if (await reader.readline()).strip() == b"":
                break
        parts = request_line.decode("latin-1").split()
        if len(parts) != 3:
            return 400, {"status": "bad_request"}
        method, path = parts[0], parts[1].split("?", 1)[0]
        if path != "/health":
            return 404, {"status": "not_found"}
        if method != "GET":
            return 405, {"status": "method_not_allowed"}
        return await self.report()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, body = await asyncio.wait_for(
                self._respond(reader), timeout=_REQUEST_TIMEOUT_SECONDS
            )
            payload = json.dumps(body, separators=(",", ":")).encode()
            reason = {200: "OK", 503: "Service Unavailable"}.get(status, "Error")
            head = (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            )
            writer.write(head.encode() + payload)
            await writer.drain()
        except (TimeoutError, ConnectionError, ValueError) as exc:
            logger.debug("Health request dropped: %s", type(exc).__name__)
        finally:
            writer.close()

    async def serve(self, host: str, port: int) -> asyncio.Server:
        return await asyncio.start_server(self.handle, host, port, limit=8192)
