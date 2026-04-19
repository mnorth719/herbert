"""Diagnostic-mode view trigger.

The full end-to-end path is now tool-based: Claude sees a `set_view` tool,
decides when to call it, and the daemon's `LocalToolDispatcher` executes
and publishes a `ViewChanged` event. That intent-driven decision can't be
simulated cleanly by the replay harness (the replay LLM has no
understanding of "enter diagnostic mode means call set_view") — the real
test is manual via `herbert dev` + a voice command, and the tool-loop
machinery itself is exercised by `tests/unit/test_llm_claude.py::TestToolUseLoop`.

What remains here is the false-positive canary: a phrase that sounds
trigger-ish but shouldn't short-circuit the LLM. The previous regex-based
implementation would have flipped the view on "herbert show me the logs"
even with trailing words; the tool approach relies on Claude's judgment.
This test fixes what the PIPELINE does with normal conversational input —
which is the same behavior the tool path gives when Claude correctly
chooses NOT to call set_view.
"""

from __future__ import annotations

from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_regular_question_flows_to_llm(run_scenario) -> None:  # type: ignore[no-untyped-def]
    """Conversational input reaches the LLM and gets a normal response.

    "Herbert show me the logs from yesterday" SOUNDS like a trigger, but
    (with the tool approach) Claude would read it as a factual question
    and not call set_view. Our replay LLM here mimics that choice by
    simply returning a text response without any tool use.
    """
    daemon, result, _ = await run_scenario(
        stt_text="herbert show me the logs from yesterday",
        llm_script=[LlmDelta(t_ms=50, text="Yesterday's logs show nothing unusual.")],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=3.0,
    )
    assert result.turn_completes[0].outcome == "success"
    assert len(daemon.session.messages) == 2
