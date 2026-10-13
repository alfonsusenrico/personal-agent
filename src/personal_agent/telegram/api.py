"""A small Telegram Bot API client: getUpdates, sendMessage, sendChatAction.

The bot token is part of every request URL, so errors carry only the HTTP status,
the Bot API error code, and exception class names; never a URL or description.
"""

from __future__ import annotations

from typing import Any

import httpx2
from pydantic import SecretStr

TELEGRAM_API_BASE = "https://api.telegram.org"
# Long polling holds a request open for its timeout; the read timeout must exceed it.
_HTTP_MARGIN_SECONDS = 15.0
_DEFAULT_TIMEOUT = httpx2.Timeout(20.0, connect=10.0)
# Telegram answers these when the chat, bot, or request itself is wrong; retrying
# cannot succeed.
PERMANENT_STATUSES = frozenset({400, 401, 403, 404})


class TelegramError(Exception):
    """A failed Bot API request. `code` is safe to log, for example http_403."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status

    @property
    def permanent(self) -> bool:
        return self.status in PERMANENT_STATUSES


class TelegramRateLimited(TelegramError):
    """Flood control: wait `retry_after_seconds` before the next request."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("http_429", status=429)
        self.retry_after_seconds = retry_after_seconds


class TelegramConflict(TelegramError):
    """Another getUpdates consumer or a webhook holds this bot's updates."""

    def __init__(self) -> None:
        super().__init__("http_409", status=409)


class TelegramApi:
    def __init__(
        self,
        token: SecretStr,
        *,
        http_client: httpx2.AsyncClient | None = None,
        base_url: str = TELEGRAM_API_BASE,
    ) -> None:
        self._http = http_client or httpx2.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._base = f"{base_url.rstrip('/')}/bot{token.get_secret_value()}"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self, method: str, payload: dict[str, Any], *, timeout: httpx2.Timeout | None = None
    ) -> Any:
        try:
            response = await self._http.post(
                f"{self._base}/{method}",
                json=payload,
                timeout=timeout or _DEFAULT_TIMEOUT,
            )
        except httpx2.TransportError as exc:
            raise TelegramError(type(exc).__name__) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
            return body.get("result")
        status = response.status_code
        if status == 429:
            raise TelegramRateLimited(_retry_after(body))
        if status == 409:
            raise TelegramConflict()
        raise TelegramError(f"http_{status}", status=status)

    async def get_me(self) -> dict[str, Any]:
        result = await self._call("getMe", {})
        if not isinstance(result, dict):
            raise TelegramError("unexpected_result")
        return result

    async def get_updates(
        self, *, offset: int | None, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        """Long-poll for updates. Calling with `offset` confirms every update below it."""
        payload: dict[str, Any] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message", "edited_message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call(
            "getUpdates",
            payload,
            timeout=httpx2.Timeout(timeout_seconds + _HTTP_MARGIN_SECONDS, connect=10.0),
        )
        if not isinstance(result, list):
            raise TelegramError("unexpected_result")
        return [update for update in result if isinstance(update, dict)]

    async def send_message(
        self, *, chat_id: str, text: str, reply_to_message_id: str | None = None
    ) -> str:
        """Send plain text and return the new message ID."""
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {
                "message_id": int(reply_to_message_id),
                # The owner may have deleted the message; the reply still matters.
                "allow_sending_without_reply": True,
            }
        result = await self._call("sendMessage", payload)
        if not isinstance(result, dict) or "message_id" not in result:
            raise TelegramError("unexpected_result")
        return str(result["message_id"])

    async def send_typing(self, *, chat_id: str) -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})


def _retry_after(body: object) -> float:
    if isinstance(body, dict):
        parameters = body.get("parameters")
        if isinstance(parameters, dict):
            value = parameters.get("retry_after")
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
                return float(value)
    # Flood control without a hint: a short, safe pause.
    return 5.0
