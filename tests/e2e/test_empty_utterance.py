"""Empty transcript short-circuits: no LLM call, no TTS, no session change."""

from __future__ import annotations

from tests.e2e.invariants import assert_state_sequence
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_empty_stt_skips_llm_and_tts(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, aout = await run_scenario(
        stt_text="",  # whisper returned nothing
        llm_script=[LlmDelta(t_ms=50, text="should not be called")],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=2.0,
    )

    # No speaking state entered — pipeline short-circuited at thinking
    assert_state_sequence(
        result.state_changes,
        ["idle->listening", "listening->thinking", "thinking->idle"],
    )
    # Session unchanged
    assert daemon.session.messages == []
    # No audio played
    assert aout.total_bytes == 0
    assert result.turn_completes[0].outcome == "success"
