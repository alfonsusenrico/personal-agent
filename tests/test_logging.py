from __future__ import annotations

import json
import logging
import sys

from personal_agent.logging_setup import _JsonFormatter, configure_logging
from tests.conftest import make_settings


def test_json_lines_carry_the_exception_class_only() -> None:
    try:
        raise ValueError("the owner's private message text")
    except ValueError:
        record = logging.LogRecord("agent", logging.ERROR, __file__, 1, "Turn failed", None, None)
        record.exc_info = sys.exc_info()

    line = _JsonFormatter().format(record)

    assert json.loads(line)["exception"] == "ValueError"
    assert "private message" not in line


def test_url_logging_libraries_are_quiet() -> None:
    configure_logging(make_settings(log_level="debug"))

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpx2").level == logging.WARNING
