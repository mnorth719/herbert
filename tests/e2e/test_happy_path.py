"""Happy path: one complete turn through every pipeline stage."""

from __future__ import annotations

from tests.e2e.invariants import (
    assert_interrupted_marker,
    assert_no_orphan_assistant,
    assert_session_alternates,
    assert_session_matches,
    assert_state_sequence,
)
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_hello_world(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _ = await run_scenario(
        stt_text="hello herbert",
        stt_duration_ms=600,
        llm_script=[
            LlmDelta(t_ms=50, text="Hello there."),
            LlmDelta(t_ms=150, text=" How are you?"),
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
    assert_session_matches(
        daemon.session.messages,
        [
            ("user", "hello herbert"),
            ("assistant", "Hello there. How are you?"),
        ],
    )
    assert_session_alternates(daemon.session.messages)
    assert_no_orphan_assistant(daemon.session.messages)
    assert_interrupted_marker(daemon.session.messages)
    assert len(result.turn_starts) == 1
    assert len(result.turn_completes) == 1
    assert result.turn_completes[0].outcome == "success"
    assert result.errors == []
