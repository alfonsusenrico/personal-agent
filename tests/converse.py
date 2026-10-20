"""Run the synthetic conversation scripts against the real gateway.

Free on the stub profile (default `reply_stub`). The paid `reply` profile needs
`--paid`, because it spends the owner's model budget. Transcripts, with tokens and
cost per call, go to the git-ignored research/eval/.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from personal_agent.db.migrate import MIGRATIONS_DIR, apply_migrations, discover
from personal_agent.gateway.api import GatewayClient
from tests.fakes import FakeClock
from tests.scripted import ScriptRun, load_script, new_agent, run_script, script_paths

EVAL_DIR = Path(__file__).resolve().parents[1] / "research" / "eval"


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scripts/converse", description=__doc__)
    parser.add_argument("--profile", default="reply_stub")
    parser.add_argument("--effort", help="override the profile's reasoning effort")
    parser.add_argument("--paid", action="store_true", help="allow a profile that costs money")
    parser.add_argument("--only", nargs="*", help="script names to run")
    return parser.parse_args()


async def _run_one(
    database_url: str, gateway: GatewayClient, path: Path, args: argparse.Namespace
) -> ScriptRun:
    schema = f"converse_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with psycopg.connect(database_url, options=f"-c search_path={schema}") as connection:
            apply_migrations(connection, discover(MIGRATIONS_DIR))
        pool = AsyncConnectionPool(
            database_url,
            connection_class=psycopg.AsyncConnection,
            kwargs={"options": f"-c search_path={schema}"},
            min_size=1,
            max_size=4,
            open=False,
        )
        await pool.open(wait=True)
        try:
            script = load_script(path)
            on_stub = args.profile == "reply_stub"
            agent = new_agent(
                pool,
                gateway.responses,
                FakeClock(datetime.now(UTC).replace(microsecond=0)),
                profile=args.profile,
                reasoning_effort=args.effort,
                # The stub answers with the script's replies, so the reply side is checked too.
                stub_replies=list(script.get("fake_replies") or []) if on_stub else None,
            )
            return await run_script(script, pool, agent, fake=on_stub)
        finally:
            await pool.close()
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _transcript(run: ScriptRun) -> str:
    lines = [f"## {run.name}", "", run.description, "", "Owner:"]
    lines += [f"- {line}" for line in run.owner_lines]
    lines += ["", "Assistant (as sent, one item per Telegram message):"]
    lines += [f"- {reply}".replace("\n", "\n  ") for reply in run.replies]
    lines += ["", "| call | status | input | cached | output | reasoning | cost USD | ms |"]
    lines += ["|---|---|---|---|---|---|---|---|"]
    for call in run.model_calls:
        lines.append(
            f"| {call['call_id']} | {call['status']} | {call['input_tokens']} | "
            f"{call['cached_input_tokens']} | {call['output_tokens']} | "
            f"{call['reasoning_tokens']} | {call['cost_usd']} | {call['latency_ms']} |"
        )
    if run.failures:
        lines += ["", "Structural failures:"] + [f"- {failure}" for failure in run.failures]
    return "\n".join(lines) + "\n"


async def main() -> int:
    args = _parse()
    if args.profile != "reply_stub" and not args.paid:
        print("This profile calls a paid model; add --paid to confirm.", file=sys.stderr)
        return 2
    database_url = os.environ.get("TEST_DATABASE_URL")
    gateway_url = os.environ.get("GATEWAY_TEST_URL")
    api_key = os.environ.get("GATEWAY_TEST_API_KEY")
    if not (database_url and gateway_url and api_key):
        print("Set TEST_DATABASE_URL, GATEWAY_TEST_URL, and GATEWAY_TEST_API_KEY.", file=sys.stderr)
        return 2
    paths = [path for path in script_paths() if not args.only or path.stem in args.only]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = f"{args.profile}-{args.effort or 'profile'}"
    out_dir = EVAL_DIR / f"{stamp}-{label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[ScriptRun] = []
    async with GatewayClient(gateway_url, api_key) as gateway:
        for path in paths:
            run = await _run_one(database_url, gateway, path, args)
            runs.append(run)
            status = "ok" if not run.failures else f"{len(run.failures)} failures"
            print(f"{run.name}: {status}")
    total = sum(
        (Decimal(call["cost_usd"]) for run in runs for call in run.model_calls if call["cost_usd"]),
        Decimal(0),
    )
    report = [
        f"# Conversation run {stamp}",
        "",
        f"Profile `{args.profile}`, reasoning effort `{args.effort or 'from profile'}`; "
        f"{len(runs)} scripts, {sum(len(run.model_calls) for run in runs)} calls, "
        f"total cost USD {total}.",
        "",
    ] + [_transcript(run) for run in runs]
    (out_dir / "transcript.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Total cost USD {total}; transcript in {out_dir.relative_to(EVAL_DIR.parents[1])}")
    return 1 if any(run.failures for run in runs) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
