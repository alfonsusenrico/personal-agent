"""Record the gateway responses the agent's client tests replay.

    uv run python tests/fixtures/gateway/record.py IMAGE_DIGEST_REFERENCE

Starts the published gateway image with a throwaway Postgres on a private Docker network,
registers a project with a `reply_stub` profile on the free stub model, makes the calls
below with the project's development key, and writes one JSON file per response here.
Everything is synthetic; the key never leaves this process and no response contains it.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx2

HERE = Path(__file__).resolve().parent
SHOWN_HEADERS = ("retry-after", "x-ratelimit-remaining", "x-concurrency-limit")
MESSAGES = [
    {"role": "developer", "content": "Synthetic context note."},
    {"role": "user", "content": "Synthetic hello"},
]


def docker(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=False)  # noqa: S603,S607
    if result.returncode != 0:
        raise SystemExit(f"docker {arguments[0]} failed: {result.stderr[-400:]}")
    return result.stdout


def save(name: str, response: httpx2.Response) -> None:
    data: dict[str, Any] = {
        "status": response.status_code,
        "headers": {key: response.headers[key] for key in SHOWN_HEADERS if key in response.headers},
    }
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data["text"] = response.text
    else:
        data["body"] = response.json()
    (HERE / f"{name}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(name, response.status_code)


def body(call_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "provider_call_id": call_id,
        "operation": "reply",
        "profile": "reply_stub",
        "input": MESSAGES,
        "stream": True,
        **extra,
    }


def main(image: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    network, database, api = f"pa-rec-{suffix}", f"pa-rec-db-{suffix}", f"pa-rec-api-{suffix}"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    password = secrets.token_urlsafe(16)
    settings = {
        "APP_ENV": "development",
        "DATABASE_URL": f"postgresql://rec:{password}@{database}:5432/rec",
        "PROJECT_KEY_PEPPER": secrets.token_urlsafe(32),
        "RESULT_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "PROVIDER_CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    }
    environment = [item for key, value in settings.items() for item in ("--env", f"{key}={value}")]
    try:
        docker("network", "create", network)
        docker(
            "run",
            "-d",
            "--name",
            database,
            "--network",
            network,
            "--env",
            "POSTGRES_USER=rec",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "--env",
            "POSTGRES_DB=rec",
            "postgres:16-alpine",
        )
        for _ in range(60):
            probe = ["docker", "exec", database, "pg_isready", "-U", "rec"]
            ready = subprocess.run(probe, capture_output=True, check=False)  # noqa: S603
            if ready.returncode == 0:
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
            "exec",
            api,
            "llm-gateway-admin",
            "project",
            "register",
            "pa_record",
            "--display-name",
            "Personal agent recording",
        )
        key = next(
            line.split()[1]
            for line in output.splitlines()
            if line.strip().startswith("development")
        )
        docker(
            "exec",
            api,
            "llm-gateway-admin",
            "project",
            "set-profile",
            "pa_record",
            "reply_stub",
            "--model",
            "stub-echo",
        )
        headers = {"Authorization": f"Bearer {key}"}
        with httpx2.Client(base_url=url, headers=headers, timeout=30, trust_env=False) as client:
            save("identity", client.get("/v1/projects/self"))
            save("stream--completed", client.post("/v1/responses", json=body("pa:rec:reply:1:a1")))
            save("stream--replayed", client.post("/v1/responses", json=body("pa:rec:reply:1:a1")))
            save(
                "stream--incomplete",
                client.post(
                    "/v1/responses", json=body("pa:rec:reply:2:a1", stub={"scenario": "incomplete"})
                ),
            )
            save(
                "stream--fail-mid-stream",
                client.post(
                    "/v1/responses",
                    json=body("pa:rec:reply:3:a1", stub={"scenario": "fail_mid_stream"}),
                ),
            )
            save(
                "problem--execution-unknown",
                client.post(
                    "/v1/responses",
                    json=body("pa:rec:reply:4:a1", stub={"scenario": "timeout_after_send"}),
                ),
            )
            save(
                "problem--provider-rate-limited",
                client.post(
                    "/v1/responses",
                    json=body("pa:rec:reply:5:a1", stub={"scenario": "rate_limited"}),
                ),
            )
            save(
                "problem--profile-not-found",
                client.post(
                    "/v1/responses",
                    json={**body("pa:rec:reply:6:a1"), "profile": "no_such_profile"},
                ),
            )
            save(
                "problem--key-invalid",
                client.get(
                    "/v1/projects/self", headers={"Authorization": "Bearer lgw_dev_invalid"}
                ),
            )
    finally:
        for name in (api, database):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)  # noqa: S603,S607
        subprocess.run(["docker", "network", "rm", network], capture_output=True, check=False)  # noqa: S603,S607


if __name__ == "__main__":
    main(sys.argv[1])
