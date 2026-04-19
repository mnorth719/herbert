"""Session reconciliation invariants after barge-in cancellation.

Two cases:
  * Before first token → user message popped (no empty assistant left behind).
  * After first token  → partial assistant response marked "[interrupted]".

Both must preserve the user/assistant alternation that the Anthropic API
requires on the next turn.
"""

from __future__ import annotations

from tests.e2e.invariants import (
    assert_interrupted_marker,
    assert_no_orphan_assistant,
    assert_session_alternates,
)
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_barge_in_before_first_token_pops_user_msg(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _ = await run_scenario(
        stt_text="never heard",
        # First delta only arrives after the barge-in fires
        llm_script=[LlmDelta(t_ms=2000, text="Too late.")],
        tts_chunks_per_sentence=5,
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
            # Barge-in while state is still "thinking"
            TimelineEvent(t_ms=200, kind="press_started"),
            TimelineEvent(t_ms=250, kind="press_ended"),
        ],
        timeout_s=6.0,
    )

    # Cancelled turn had zero tokens → user msg should have been popped.
    # Second turn's LLM script was never rearmed, so it also produces no
    # response. The net result: session is empty OR has the second turn's
    # user + nothing-yet.
    roles = [m.role for m in daemon.session.messages]
    # Critical property: no orphan assistant, no double-user pair
    assert_session_alternates(daemon.session.messages)
    assert_no_orphan_assistant(daemon.session.messages)
    assert all(r in ("user", "assistant") for r in roles)
    assert "cancelled" in [c.outcome for c in result.turn_completes]


async def test_barge_in_after_first_token_marks_interrupted(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, _, _ = await run_scenario(
        stt_text="speak up",
        # First delta lands fast; barge-in fires after some tokens have been received
        llm_script=[
            LlmDelta(t_ms=30, text="The weather is "),
            LlmDelta(t_ms=100, text="absolutely fine "),
            LlmDelta(t_ms=5000, text="today. "),  # never arrives
        ],
        tts_chunks_per_sentence=20,  # keeps TTS awake long enough for barge-in
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
            # Barge-in after first-token has landed, before stream completes
            TimelineEvent(t_ms=300, kind="press_started"),
            TimelineEvent(t_ms=350, kind="press_ended"),
        ],
        timeout_s=6.0,
    )

    # The cancelled assistant message should end with [interrupted]
    interrupted = [
        m
        for m in daemon.session.messages
        if m.role == "assistant" and m.content.endswith("[interrupted]")
    ]
    assert len(interrupted) == 1, (
        f"expected exactly one [interrupted] assistant, got: "
        f"{[(m.role, m.content) for m in daemon.session.messages]}"
    )
    assert_session_alternates(daemon.session.messages)
    assert_no_orphan_assistant(daemon.session.messages)
    assert_interrupted_marker(daemon.session.messages)
