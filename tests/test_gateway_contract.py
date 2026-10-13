"""The reply call against a real gateway's free stub profile.

Runs only when GATEWAY_TEST_URL and GATEWAY_TEST_API_KEY (a development key whose
contract has a `reply_stub` profile on the stub model) are set.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from typing import Any

import pytest
from llm_gateway_client import Completed, GatewayClient, InvalidRequestError, TextDelta

from personal_agent.conversation.context import build_reply_request
from personal_agent.conversation.models import GenerationPlan, MessageRow, TurnInput
from tests.fakes import START
from tests.test_context import _event

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not (os.environ.get("GATEWAY_TEST_URL") and os.environ.get("GATEWAY_TEST_API_KEY")),
        reason="GATEWAY_TEST_URL and GATEWAY_TEST_API_KEY not set",
    ),
]


async def test_reply_stream_is_accepted_replayed_and_guarded() -> None:
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
    call_id = f"pa:contract-{uuid.uuid4().hex[:12]}:reply:1:a1"
    async with GatewayClient(
        os.environ["GATEWAY_TEST_URL"], os.environ["GATEWAY_TEST_API_KEY"]
    ) as gateway:
        arguments: dict[str, Any] = {
            "provider_call_id": call_id,
            "operation": "reply",
            "input": request.input,
            "profile": "reply_stub",
            "instructions": request.instructions,
            "max_output_tokens": 2_000,
            "prompt_cache_key": request.prompt_cache_key,
        }
        first = [event async for event in gateway.responses.stream(**arguments)]
        again = [event async for event in gateway.responses.stream(**arguments)]
        changed = {**arguments, "input": [*request.input, {"role": "user", "content": "x"}]}
        with pytest.raises(InvalidRequestError) as conflict:
            _ = [event async for event in gateway.responses.stream(**changed)]

    streamed = "".join(event.delta for event in first if isinstance(event, TextDelta))
    final = first[-1]
    assert isinstance(final, Completed) and final.result.status == "completed"
    assert streamed == final.result.output_text and streamed.startswith("stub:")
    # A resubmission replays the stored answer as one final event.
    assert len(again) == 1 and isinstance(again[0], Completed) and again[0].result.replayed
    assert again[0].result.output_text == final.result.output_text
    assert conflict.value.code == "idempotency_conflict"
