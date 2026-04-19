"""Multi-sentence streaming: three sentences handed to TTS in order."""

from __future__ import annotations

from tests.e2e.invariants import assert_session_alternates, assert_state_sequence
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_three_sentences_streamed_in_order(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _ = await run_scenario(
        stt_text="tell me three things",
        llm_script=[
            LlmDelta(t_ms=40, text="First, "),
            LlmDelta(t_ms=80, text="the sun is yellow. "),
            LlmDelta(t_ms=160, text="Second, grass is green. "),
            LlmDelta(t_ms=240, text="Third, water is wet."),
        ],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
    )

    assert_state_sequence(
        result.state_changes,
        [
            "idle->listening",
            "listening->thinking",
            "thinking->speaking",
            "speaking->idle",
        ],
    )
    # Three sentences composed from the deltas
    expected_reply = "First, the sun is yellow. Second, grass is green. Third, water is wet."
    assert daemon.session.messages[-1].content == expected_reply
    assert_session_alternates(daemon.session.messages)
    assert result.turn_completes[0].outcome == "success"
