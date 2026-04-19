"""Barge-in during speaking: cancel current turn, start a new listening."""

from __future__ import annotations

from tests.e2e.invariants import (
    assert_interrupted_marker,
    assert_no_orphan_assistant,
    assert_session_alternates,
)
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_press_during_speaking_cancels_and_starts_new_turn(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _aout = await run_scenario(
        stt_text="tell me a long story",
        # Slow-drip deltas — the turn is still streaming when barge-in fires
        llm_script=[
            LlmDelta(t_ms=30, text="Once upon a time "),
            LlmDelta(t_ms=100, text="there was a little robot. "),
            LlmDelta(t_ms=400, text="It lived on a shelf and "),
            LlmDelta(t_ms=600, text="rarely spoke."),
        ],
        tts_chunks_per_sentence=20,  # keeps "speaking" alive long enough
        tts_per_chunk_ms=15,
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
            # Barge-in while Herbert is speaking
            TimelineEvent(t_ms=500, kind="press_started"),
            TimelineEvent(t_ms=550, kind="press_ended"),
        ],
        timeout_s=8.0,
    )

    # We saw at least one cancelled and one successful turn
    outcomes = [c.outcome for c in result.turn_completes]
    assert "cancelled" in outcomes
    assert outcomes.count("success") + outcomes.count("cancelled") >= 2

    # Session survives a barge-in with valid alternation
    assert_session_alternates(daemon.session.messages)
    assert_no_orphan_assistant(daemon.session.messages)
    assert_interrupted_marker(daemon.session.messages)
    # After the cancelled turn's second iteration completes, state returns to idle
    assert daemon.state == "idle"
