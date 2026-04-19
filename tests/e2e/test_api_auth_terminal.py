"""Auth failure is terminal: daemon sits in error state until the next press."""

from __future__ import annotations

from tests.e2e.invariants import assert_session_alternates
from tests.e2e.replay_transport import TimelineEvent


class _AuthError(Exception):
    """Stand-in for anthropic.AuthenticationError."""


async def test_auth_error_enters_terminal_error_state(run_scenario) -> None:  # type: ignore[no-untyped-def]
    daemon, result, _ = await run_scenario(
        stt_text="please respond",
        llm_raise_on_open=_AuthError("invalid_api_key: authentication failed"),
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=3.0,
    )

    assert daemon.state == "error"
    assert len(result.errors) == 1
    assert result.errors[0].error_class == "api_auth"
    # User message rolled back so the next successful turn starts clean
    assert_session_alternates(daemon.session.messages)
    assert result.turn_completes[0].outcome == "error"


async def test_manual_retry_from_error_next_press(run_scenario) -> None:  # type: ignore[no-untyped-def]
    """After error, next press transitions back to listening (manual retry)."""
    # Simulate: first turn fails mid-open, second press would retry. Our
    # replay client is fixed per scenario, so we can't swap it mid-run.
    # Instead, just verify that a press-from-error sequence fires the
    # listening transition — the actual re-execution runs the SAME failing
    # client, confirming the daemon doesn't get "stuck" in error state.
    _daemon, result, _ = await run_scenario(
        stt_text="retry me",
        llm_raise_on_open=_AuthError("auth failed"),
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
            TimelineEvent(t_ms=500, kind="press_started"),
            TimelineEvent(t_ms=550, kind="press_ended"),
        ],
        timeout_s=4.0,
    )

    # Two turn attempts, both errored
    outcomes = [c.outcome for c in result.turn_completes]
    assert outcomes.count("error") == 2
    # Observed at least one error → listening transition (manual retry path)
    pairs = [(e.from_state, e.to_state) for e in result.state_changes]
    assert ("error", "listening") in pairs, f"no error→listening transition in {pairs}"
