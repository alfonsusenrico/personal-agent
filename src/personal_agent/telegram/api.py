"""A small Telegram Bot API client for the methods the worker uses.

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
        if status == 400 and _mentions_effect(body):
            raise TelegramError("effect_invalid", status=400)
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
            "allowed_updates": [
                "message",
                "edited_message",
                "callback_query",
                "poll_answer",
                "stopped_message_generation",
            ],
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
            payload["reply_parameters"] = _reply_parameters(reply_to_message_id)
        return _message_id(await self._call("sendMessage", payload))

    async def send_rich_message(
        self,
        *,
        chat_id: str,
        markdown: str,
        reply_to_message_id: str | None = None,
        effect_id: str | None = None,
    ) -> str:
        """Send Telegram Rich Markdown and return the new message ID."""
        payload: dict[str, Any] = {"chat_id": chat_id, "rich_message": {"markdown": markdown}}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = _reply_parameters(reply_to_message_id)
        if effect_id is not None:
            payload["message_effect_id"] = effect_id
        return _message_id(await self._call("sendRichMessage", payload))

    async def send_draft(self, *, chat_id: str, draft_id: int, markdown: str) -> None:
        """Show a streaming preview with a Stop button.

        Always a rich draft: the final sendRichMessage clears only a rich draft, so a
        plain sendMessageDraft would stay in the chat beside the reply.
        """
        await self._call(
            "sendRichMessageDraft",
            {
                "chat_id": int(chat_id),
                "draft_id": draft_id,
                "rich_message": {"markdown": markdown},
                "can_stop": True,
            },
        )

    async def send_typing(self, *, chat_id: str) -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    async def edit_rich_message(self, *, chat_id: str, message_id: str, markdown: str) -> None:
        await self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "rich_message": {"markdown": markdown},
            },
        )

    async def answer_callback(self, *, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)

    async def set_reaction(self, *, chat_id: str, message_id: str, emoji: str) -> None:
        await self._call(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )

    async def send_poll(
        self, *, chat_id: str, question: str, options: list[str], allows_multiple: bool
    ) -> tuple[str, str]:
        """Send a non-anonymous poll, so votes come back; returns (message ID, poll ID)."""
        result = await self._call(
            "sendPoll",
            {
                "chat_id": chat_id,
                "question": question,
                "options": [{"text": option} for option in options],
                "is_anonymous": False,
                "allows_multiple_answers": allows_multiple,
            },
        )
        poll = result.get("poll") if isinstance(result, dict) else None
        if not isinstance(poll, dict) or "id" not in poll:
            raise TelegramError("unexpected_result")
        return _message_id(result), str(poll["id"])


def _reply_parameters(message_id: str) -> dict[str, Any]:
    # The owner may have deleted the message; the reply still matters.
    return {"message_id": int(message_id), "allow_sending_without_reply": True}


def _message_id(result: object) -> str:
    if not isinstance(result, dict) or "message_id" not in result:
        raise TelegramError("unexpected_result")
    return str(result["message_id"])


def _mentions_effect(body: object) -> bool:
    # Only classified, never logged: descriptions can quote request details.
    description = body.get("description") if isinstance(body, dict) else None
    return isinstance(description, str) and "effect" in description.lower()


def _retry_after(body: object) -> float:
    if isinstance(body, dict):
        parameters = body.get("parameters")
        if isinstance(parameters, dict):
            value = parameters.get("retry_after")
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
                return float(value)
    # Flood control without a hint: a short, safe pause.
    return 5.0
