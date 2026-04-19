"""Turn + TurnSpan container behavior."""

from __future__ import annotations

import asyncio

from herbert.turn import Turn, TurnSpan


def test_turn_id_is_unique() -> None:
    t1 = Turn()
    t2 = Turn()
    assert t1.turn_id != t2.turn_id


def test_turn_owns_events() -> None:
    t = Turn()
    assert isinstance(t.release_event, asyncio.Event)
    assert isinstance(t.cancel_event, asyncio.Event)
    assert not t.release_event.is_set()
    assert not t.cancel_event.is_set()


def test_request_cancel_sets_both_events() -> None:
    t = Turn()
    t.request_cancel()
    assert t.cancel_event.is_set()
    # release_event set too so an in-progress capture exits immediately
    assert t.release_event.is_set()


def test_span_records_and_marks_misses() -> None:
    span = TurnSpan(turn_id="t1")
    span.record("stt", 1100)
    span.record("llm_ttft", 450)
    span.mark_miss("stt")
    span.mark_miss("stt")  # idempotent — not added twice
    assert span.stage_durations == {"stt": 1100, "llm_ttft": 450}
    assert span.misses == ["stt"]


def test_turn_shares_span_turn_id() -> None:
    t = Turn()
    assert t.span.turn_id == t.turn_id
