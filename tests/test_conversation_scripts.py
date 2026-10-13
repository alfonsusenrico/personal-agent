"""Every synthetic conversation script, run free against the fake gateway."""

from __future__ import annotations

from pathlib import Path

import pytest
from psycopg_pool import AsyncConnectionPool

from tests.fakes import START, FakeClock, FakeResponses
from tests.scripted import load_script, new_agent, run_script, script_paths

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def test_scripts_exist_and_parse() -> None:
    paths = script_paths()

    assert len(paths) >= 10
    for path in paths:
        assert load_script(path)["name"] == path.stem


@pytest.mark.parametrize("path", script_paths(), ids=lambda path: path.stem)
async def test_script_outcome(path: Path, pool: AsyncConnectionPool) -> None:
    agent = new_agent(pool, FakeResponses(), FakeClock(START))

    run = await run_script(load_script(path), pool, agent)

    assert run.failures == []
    assert run.replies, "every script gets at least one reply"
