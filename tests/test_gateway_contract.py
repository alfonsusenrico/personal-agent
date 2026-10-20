"""The real reply call against a published gateway image, as a black box (opt-in).

Set PA_CONTRACT_IMAGE to a gateway image pinned by digest. A throwaway Postgres and the
image run on a private Docker network with synthetic settings; a project with a
`reply_stub` profile on the free stub model is registered through the image's admin CLI.
The test sends the reply request the agent builds and checks that it is accepted, replayed,
and guarded, and that every field the recorded responses contain is still returned.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from personal_agent.conversation.context import build_reply_request
from personal_agent.conversation.models import GenerationPlan, MessageRow, TurnInput
from personal_agent.gateway.api import Completed, GatewayClient, InvalidRequestError, TextDelta
from tests.fakes import START
from tests.test_context import _event

IMAGE = os.environ.get("PA_CONTRACT_IMAGE")
FIXTURES = Path(__file__).parent / "fixtures" / "gateway"
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not IMAGE, reason="set PA_CONTRACT_IMAGE to a gateway image digest"),
]


def docker(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=False)  # noqa: S603,S607
    if result.returncode != 0:
        raise RuntimeError(f"docker {arguments[0]} failed: {result.stderr[-500:]}")
    return result.stdout


@pytest.fixture(scope="module")
def gateway() -> Iterator[tuple[str, str]]:
    image = os.environ["PA_CONTRACT_IMAGE"]  # the module is skipped without it
    suffix = uuid.uuid4().hex[:10]
    network, database, api = f"pa-contract-{suffix}", f"pa-db-{suffix}", f"pa-api-{suffix}"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    password = secrets.token_urlsafe(16)
    settings = {
        "APP_ENV": "development",
        "DATABASE_URL": f"postgresql://contract:{password}@{database}:5432/contract",
        "PROJECT_KEY_PEPPER": secrets.token_urlsafe(32),
        "RESULT_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "PROVIDER_CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    environment = [item for key, value in settings.items() for item in ("--env", f"{key}={value}")]
    docker("network", "create", network)
    try:
        docker(
            "run",
            "-d",
            "--name",
            database,
            "--network",
            network,
            "--env",
            "POSTGRES_USER=contract",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--env",
            "POSTGRES_DB=contract",
            "postgres:16-alpine",
        )
        probe_command = ["docker", "exec", database, "pg_isready", "-U", "contract"]
        for _ in range(60):
            if subprocess.run(probe_command, capture_output=True, check=False).returncode == 0:  # noqa: S603
                break
            time.sleep(1)
        docker(
            "run",
            "--rm",
            "--network",
            network,
            *environment,
            image,
            "sh",
            "-c",
            "llm-gateway-migrate && llm-gateway-admin models sync",
        )
        docker(
            "run",
            "-d",
            "--name",
            api,
            "--network",
            network,
            "-p",
            f"127.0.0.1:{port}:8000",
            *environment,
            image,
        )
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                if httpx2.get(url + "/health/ready", timeout=2, trust_env=False).status_code == 200:
                    break
            except httpx2.TransportError:
                pass
            time.sleep(1)
        output = docker(
            "exec", api, "llm-gateway-admin", "project", "register", "pa_contract",
            "--display-name", "Personal agent contract",
        )  # fmt: skip
        key = next(
            line.split()[1]
            for line in output.splitlines()
            if line.strip().startswith("development")
        )
        docker(
            "exec", api, "llm-gateway-admin", "project", "set-profile", "pa_contract",
            "reply_stub", "--model", "stub-echo",
        )  # fmt: skip
        yield url, key
    finally:
        for name in (api, database):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)  # noqa: S603,S607
        subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)  # noqa: S603,S607


def recorded_result_fields() -> set[str]:
    text = json.loads((FIXTURES / "stream--completed.json").read_text())["text"]
    final = text.split("event: response.completed\ndata: ", 1)[1].split("\n", 1)[0]
    return set(json.loads(final))


async def test_reply_stream_is_accepted_replayed_and_guarded(gateway: tuple[str, str]) -> None:
    url, key = gateway
    plan = GenerationPlan(1, "contract", 1, 1, 1, START, 2)
    window = [
        MessageRow(1, "user", "earlier", START - timedelta(hours=1), "Asia/Jakarta"),
        MessageRow(2, "assistant", "earlier reply", START - timedelta(hours=1), "Asia/Jakarta"),
    ]
    request = build_reply_request(
        TurnInput(plan, "Asia/Jakarta", "1001", [_event(3, "contract check")], window),
        window_budget_tokens=6_000,
        offline_after=timedelta(minutes=5),
    )
    arguments: dict[str, Any] = {
        "provider_call_id": f"pa:contract-{uuid.uuid4().hex[:12]}:reply:1:a1",
        "operation": "reply",
        "input": request.input,
        "profile": "reply_stub",
        "instructions": request.instructions,
        "max_output_tokens": 2_000,
        "prompt_cache_key": request.prompt_cache_key,
    }
    async with GatewayClient(url, key) as client:
        identity = await client.whoami()
        first = [event async for event in client.responses.stream(**arguments)]
        again = [event async for event in client.responses.stream(**arguments)]
        changed = {**arguments, "input": [*request.input, {"role": "user", "content": "x"}]}
        with pytest.raises(InvalidRequestError) as conflict:
            _ = [event async for event in client.responses.stream(**changed)]

    assert identity.project_id == "pa_contract"
    streamed = "".join(event.delta for event in first if isinstance(event, TextDelta))
    final = first[-1]
    assert isinstance(final, Completed) and final.result.status == "completed"
    assert streamed == final.result.output_text and streamed.startswith("stub:")
    # A resubmission replays the stored answer as one final event.
    assert len(again) == 1 and isinstance(again[0], Completed) and again[0].result.replayed
    assert again[0].result.output_text == final.result.output_text
    assert conflict.value.code == "idempotency_conflict"


async def test_the_live_image_still_returns_every_recorded_field(gateway: tuple[str, str]) -> None:
    url, key = gateway
    body = {
        "provider_call_id": f"pa:contract-{uuid.uuid4().hex[:12]}:reply:1:a1",
        "operation": "reply",
        "profile": "reply_stub",
        "input": [{"role": "user", "content": "Synthetic"}],
        "stream": True,
    }
    async with httpx2.AsyncClient(timeout=30, trust_env=False) as http:
        response = await http.post(
            url + "/v1/responses", json=body, headers={"Authorization": f"Bearer {key}"}
        )
    final = response.text.split("event: response.completed\ndata: ", 1)[1].split("\n", 1)[0]
    missing = recorded_result_fields() - set(json.loads(final))
    assert not missing, f"fields missing from the live gateway: {sorted(missing)}"
