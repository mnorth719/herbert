"""Anthropic 529/5xx fires mid-stream. Daemon enters error state.

v1 Unit 7 does NOT auto-retry (deferred to Unit 11 per plan R16 scope):
the daemon stays in `error` until the user presses again. This test
encodes that current contract; when Unit 11 adds exponential backoff,
the assertion here will tighten to expect the auto-recovery path.
"""

from __future__ import annotations

from tests.e2e.invariants import assert_no_orphan_assistant, assert_session_alternates
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


class _OverloadedError(Exception):
    """Stand-in for anthropic.InternalServerError (529 overloaded)."""


async def test_529_enters_error_state_until_next_press(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _ = await run_scenario(
        stt_text="hey",
        llm_script=[
            LlmDelta(t_ms=30, text="Hello th"),
            LlmDelta(t_ms=80, error=_OverloadedError("529 overloaded_error")),
        ],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=4.0,
    )

    # Ends up parked in error state
    assert daemon.state == "error"
    # The error was classified as network_transient (5xx family)
    assert len(result.errors) == 1
    assert result.errors[0].error_class == "network_transient"
    # Session cleanup: the partial assistant response left by the
    # cancelled-mid-stream reconcile should preserve alternation.
    assert_session_alternates(daemon.session.messages)
    assert_no_orphan_assistant(daemon.session.messages)
    # Turn completes with outcome="error"
    assert [c.outcome for c in result.turn_completes] == ["error"]
