"""Voice-triggered diagnostic view — Unit 12."""

from __future__ import annotations

from herbert.events import ViewChanged
from tests.e2e.replay_transport import LlmDelta, TimelineEvent


async def test_herbert_show_me_the_logs_flips_view(run_scenario) -> None:  # type: ignore[no-untyped-def]
    """The matched utterance should short-circuit the LLM + flip the view."""
    daemon, result, _ = await run_scenario(
        stt_text="herbert show me the logs",
        llm_script=[LlmDelta(t_ms=50, text="This should not be called.")],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=3.0,
    )
    # Session is untouched — no LLM turn happened
    assert daemon.session.messages == []
    # A ViewChanged(view="diagnostic") fired
    view_events = [e for e in result.all_events if isinstance(e, ViewChanged)]
    assert len(view_events) == 1
    assert view_events[0].view == "diagnostic"
    # The turn completed (didn't error)
    assert result.turn_completes[0].outcome == "success"


async def test_false_positive_lets_llm_run(run_scenario) -> None:  # type: ignore[no-untyped-def]
    """'herbert show me the logs from yesterday' is NOT a whole-utterance match.

    Passes in Unit 7 (no diagnostic mode yet): the transcript flows to LLM
    normally. When Unit 12 adds the regex matcher, this test still has to
    pass — it's the canary that distinguishes a precise matcher from a
    substring one. Kept live (not xfail) so regressions surface immediately.
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
