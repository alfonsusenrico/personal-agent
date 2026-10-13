"""Process logging: JSON lines in production, readable lines in development.

Log records carry identifiers, counts, durations, and states only. Messages,
memories, email, calendar data, prompts, model output, and keys are never logged.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from personal_agent.settings import Settings

# HTTP client libraries log full request URLs at INFO and DEBUG, and some provider
# URLs carry credentials (the Telegram bot token is part of the path).
_URL_LOGGING_LIBRARIES = ("httpx", "httpcore", "httpx2", "httpcore2")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            # Only the exception class: messages can echo personal data or values.
            entry["exception"] = record.exc_info[0].__name__
        return json.dumps(entry, separators=(",", ":"))


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    if settings.app_env == "production":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    for name in _URL_LOGGING_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
