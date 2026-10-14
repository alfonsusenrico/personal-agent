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
    script = load_script(path)
    responses = FakeResponses()
    responses.script = list(script.get("fake_replies") or [])
    agent = new_agent(pool, responses, FakeClock(START))

    run = await run_script(script, pool, agent, fake=True)

    assert run.failures == []
