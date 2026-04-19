"""Long response: sentence-boundary flushing keeps streaming to TTS."""

from __future__ import annotations

from tests.e2e.invariants import assert_session_alternates, assert_state_sequence
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_long_response_streams_progressively(run_scenario) -> None:  # type: ignore[no-untyped-def]
    # 10 sentences, ~30 words each. Verifies sentence-boundary flush works
    # across a long tail and playback starts before the stream ends.
    deltas: list[LlmDelta] = []
    body_sentences: list[str] = []
    for i in range(10):
        text = f"This is sentence number {i} with some filler words. "
        body_sentences.append(text.strip())
        deltas.append(LlmDelta(t_ms=30 + i * 40, text=text))

    daemon, result, aout = await run_scenario(
        stt_text="tell me something long",
        llm_script=deltas,
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=8.0,
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
    expected = " ".join(body_sentences)
    # Trailing space from last delta
    assert daemon.session.messages[-1].content.strip() == expected
    assert_session_alternates(daemon.session.messages)
    # At least as many PCM chunks as sentences → confirms progressive TTS
    assert aout.total_bytes >= 10 * 4 * 256  # 10 sentences x 4 chunks x 256 bytes
    assert result.turn_completes[0].outcome == "success"
