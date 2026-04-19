"""End-to-end Claude call against a real Anthropic key.

Gated behind `HERBERT_LIVE=1` plus a valid `ANTHROPIC_API_KEY` — run
manually when iterating on prompt or latency behaviour, skipped in CI so
we don't burn credits.
"""

from __future__ import annotations

import os
import time

import pytest

from herbert.llm.claude import LlmTurnState, stream_turn
from herbert.session import InMemorySession

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("HERBERT_LIVE") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="set HERBERT_LIVE=1 and ANTHROPIC_API_KEY to exercise real Anthropic",
)
async def test_minimal_turn() -> None:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    session = InMemorySession()
    state = LlmTurnState()
    persona = "You are Herbert. Reply in one short sentence."

    start = time.perf_counter()
    sentences: list[str] = []
    async for sentence in stream_turn(
        "Say hello in five words.",
        session,
        persona,
        client=client,
        state=state,
    ):
        sentences.append(sentence)
    total = int((time.perf_counter() - start) * 1000)

    assert len(sentences) >= 1
    assert all(s.strip() for s in sentences)
    # Loose ceiling — not a pass/fail for R6, just a sanity bar.
    assert state.ttft_ms is not None and state.ttft_ms < 5000
    assert total < 10000
    # Session round-trip
    assert [m.role for m in session.messages] == ["user", "assistant"]
