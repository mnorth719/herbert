"""WiFi drop (connection error) mid-stream. Daemon enters error state.

Same terminal shape as the 5xx case in v1 Unit 7: auto-recovery with
exponential backoff is a Unit 11 deliverable. Here we just verify the
classification (`wifi_down`) and the error state entry.
"""

from __future__ import annotations

from tests.e2e.invariants import assert_no_orphan_assistant, assert_session_alternates
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


class _ConnectionDropped(Exception):
    """Stand-in for a mid-stream network exception."""


async def test_connection_drop_mid_stream_enters_error(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _ = await run_scenario(
        stt_text="talk to me",
        llm_script=[
            LlmDelta(t_ms=40, text="Hel"),
            LlmDelta(t_ms=90, error=_ConnectionDropped("connection reset by peer")),
        ],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=3.0,
    )

    assert daemon.state == "error"
    assert len(result.errors) == 1
    assert result.errors[0].error_class == "wifi_down"
    assert_session_alternates(daemon.session.messages)
    assert_no_orphan_assistant(daemon.session.messages)
