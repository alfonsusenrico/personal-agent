"""The bot's command menu: only the commands this assistant handles."""

from __future__ import annotations

import logging

from personal_agent.telegram.api import TelegramApi, TelegramError

COMMANDS = [
    {"command": "profile", "description": "Show what I know about you"},
    {"command": "stop", "description": "Stop the reply being written"},
]
logger = logging.getLogger(__name__)


async def sync_commands(telegram: TelegramApi) -> None:
    """Replace the menu when it differs, so restarts do not repeat the call. A failure
    leaves the old menu and is only logged: the commands still work when typed."""
    try:
        if await telegram.get_my_commands() == COMMANDS:
            return
        await telegram.set_my_commands(COMMANDS)
    except TelegramError as exc:
        logger.warning("Command menu not updated: %s", exc.code)
        return
    logger.info("Command menu updated")
