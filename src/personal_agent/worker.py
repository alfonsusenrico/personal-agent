"""The worker: Telegram poller, turn runner, and sender in one asyncio process.

Postgres is the queue. A session-level advisory lock keeps a second worker on the
same database from starting; Telegram also refuses two concurrent getUpdates.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import timedelta

import psycopg
from llm_gateway_client import AuthenticationError, GatewayClient, GatewayError

from personal_agent.clock import utc_now
from personal_agent.conversation.runner import RunnerConfig, TurnRunner
from personal_agent.db.pool import create_pool
from personal_agent.logging_setup import configure_logging
from personal_agent.settings import SettingsError, WorkerSettings, load_worker_settings
from personal_agent.telegram.api import TelegramApi, TelegramError
from personal_agent.telegram.commands import sync_commands
from personal_agent.telegram.drafts import Drafts
from personal_agent.telegram.poller import Poller
from personal_agent.telegram.sender import Sender

# An arbitrary constant owned by this module; the migration runner uses another.
WORKER_LOCK_KEY = 7_402_120_002
EXIT_CONFIG = 2
EXIT_LOCKED = 3
logger = logging.getLogger(__name__)


async def acquire_worker_lock(database_url: str) -> psycopg.AsyncConnection | None:
    """A connection holding the worker lock for the life of the process, or None."""
    connection = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
    cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (WORKER_LOCK_KEY,))
    row = await cursor.fetchone()
    if row is None or row[0] is not True:
        await connection.close()
        return None
    return connection


async def _check_services(telegram: TelegramApi, gateway: GatewayClient) -> bool:
    try:
        bot = await telegram.get_me()
    except TelegramError as exc:
        logger.error("Telegram rejected the bot token check: %s", exc.code)
        return False
    logger.info("Telegram bot %s ready", bot.get("id"))
    try:
        identity = await gateway.whoami()
    except AuthenticationError as exc:
        logger.error("Gateway rejected the project key: %s", exc.code)
        return False
    except GatewayError as exc:
        # Calls retry on their own; the gateway may still be starting.
        logger.warning("Gateway not reachable yet: %s", exc.code)
    else:
        logger.info("Gateway project %s (%s)", identity.project_id, identity.environment)
    return True


async def run(settings: WorkerSettings) -> int:
    lock = await acquire_worker_lock(str(settings.database_url))
    if lock is None:
        logger.error("Another worker holds the worker lock; exiting")
        return EXIT_LOCKED
    pool = create_pool(settings)
    telegram = TelegramApi(settings.telegram_bot_token)
    gateway = GatewayClient(str(settings.gateway_url), settings.gateway_api_key.get_secret_value())
    try:
        await pool.open(wait=True, timeout=10)
        if not await _check_services(telegram, gateway):
            return EXIT_CONFIG
        await sync_commands(telegram)
        turn_wakeup = asyncio.Event()
        outbox_wakeup = asyncio.Event()
        runner = TurnRunner(
            pool,
            gateway.responses,
            RunnerConfig(
                reply_profile=settings.reply_profile,
                default_timezone=settings.default_timezone,
                debounce=timedelta(seconds=settings.turn_debounce_seconds),
                window_budget_tokens=settings.recent_window_tokens,
                offline_after=timedelta(seconds=settings.offline_notice_after_seconds),
            ),
            clock=utc_now,
            drafts=Drafts(telegram, interval_seconds=settings.draft_interval_seconds),
            on_answer=outbox_wakeup.set,
        )

        def on_stored() -> None:
            # A stored press can queue an outbox row on its own (the bot's name).
            turn_wakeup.set()
            outbox_wakeup.set()

        poller = Poller(
            pool,
            telegram,
            allowed_user_ids=settings.telegram_allowed_user_ids,
            default_timezone=settings.default_timezone,
            clock=utc_now,
            on_stored=on_stored,
            on_stop=runner.request_stop,
        )
        sender = Sender(pool, telegram, clock=utc_now)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        tasks = [
            asyncio.create_task(poller.run_forever(), name="poller"),
            asyncio.create_task(runner.run_forever(turn_wakeup), name="runner"),
            asyncio.create_task(sender.run_forever(outbox_wakeup), name="sender"),
        ]
        logger.info("Worker started (revision %s)", settings.app_revision)
        stopped = asyncio.create_task(stop.wait())
        await asyncio.wait([*tasks, stopped], return_when=asyncio.FIRST_COMPLETED)
        crashed = [task for task in tasks if task.done()]
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        stopped.cancel()
        if crashed:
            # The loops never return on their own; any that ended has failed.
            for task, result in zip(tasks, results, strict=True):
                if task in crashed:
                    logger.error("Worker loop %s ended: %s", task.get_name(), type(result).__name__)
            return 1
        logger.info("Worker stopped")
        return 0
    finally:
        await gateway.aclose()
        await telegram.aclose()
        await pool.close()
        await lock.close()


def main() -> int:
    try:
        settings = load_worker_settings()
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG
    configure_logging(settings)
    return asyncio.run(run(settings))


if __name__ == "__main__":
    raise SystemExit(main())
