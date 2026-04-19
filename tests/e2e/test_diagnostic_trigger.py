"""Voice-triggered diagnostic view.

Unit 7 does NOT implement the regex trigger — that lands in Unit 12 with
`persona.py` / `diagnostic/triggers.py`. These scenarios are written now
so Unit 12's "done" is simply `pytest tests/e2e/` green with the xfails
flipping to passes.
"""

from __future__ import annotations

import pytest

from tests.e2e.replay_transport import LlmDelta, TimelineEvent


@pytest.mark.xfail(reason="diagnostic trigger regex lands in Unit 12")
async def test_herbert_show_me_the_logs_flips_view(run_scenario) -> None:  # type: ignore[no-untyped-def]
    # When Unit 12 lands: daemon should intercept the transcript BEFORE
    # the LLM call, publish ViewChanged(view="diagnostic"), and NOT touch
    # the session. Until then, this fixture drives through the LLM as
    # normal and the test fails — which is the xfail we want.
    daemon, _, _ = await run_scenario(
        stt_text="herbert show me the logs",
        llm_script=[LlmDelta(t_ms=50, text="This should not be called.")],
        timeline=[
            TimelineEvent(t_ms=0, kind="press_started"),
            TimelineEvent(t_ms=50, kind="press_ended"),
        ],
        timeout_s=3.0,
    )
    # Post-Unit-12 expectation: session unchanged, view switched to diagnostic
    assert daemon.session.messages == []


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
