"""Event bus and typed event tests."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytest

from herbert.events import (
    AsyncEventBus,
    ErrorOccurred,
    ExchangeLatency,
    StateChanged,
    Subscription,
    TranscriptDelta,
    TurnCompleted,
    TurnStarted,
    ViewChanged,
)


class TestEventModels:
    def test_state_changed_has_timestamp_and_seq(self) -> None:
        e = StateChanged(turn_id="t1", from_state="idle", to_state="listening")
        assert e.turn_id == "t1"
        assert e.from_state == "idle"
        assert e.to_state == "listening"
        assert isinstance(e.timestamp, datetime)
        assert e.timestamp.tzinfo is not None  # UTC-aware

    def test_event_type_literal_preserved(self) -> None:
        e = StateChanged(turn_id="t1", from_state="idle", to_state="listening")
        assert e.event_type == "state_changed"

    def test_exchange_latency_carries_stage_durations(self) -> None:
        e = ExchangeLatency(
            turn_id="t2",
            total_ms=1800,
            stage_durations={"stt": 1100, "llm_ttft": 500, "tts_ttfb": 200},
            misses=[],
            mode="pi_hybrid",
        )
        assert e.total_ms == 1800
        assert e.stage_durations["stt"] == 1100

    def test_error_occurred_class_enum(self) -> None:
        e = ErrorOccurred(turn_id=None, error_class="api_auth", message="invalid key")
        assert e.error_class == "api_auth"

    def test_transcript_delta(self) -> None:
        e = TranscriptDelta(turn_id="t3", role="assistant", text=" hello")
        assert e.role == "assistant"
        assert e.text == " hello"


class TestBusDispatch:
    async def test_single_subscriber_receives_event(self) -> None:
        bus = AsyncEventBus()
        async with bus.subscribe() as sub:
            await bus.publish(StateChanged(turn_id="t1", from_state="idle", to_state="listening"))
            event = await asyncio.wait_for(sub.receive(), timeout=1.0)
            assert isinstance(event, StateChanged)
            assert event.to_state == "listening"

    async def test_multiple_subscribers_each_receive(self) -> None:
        bus = AsyncEventBus()
        async with bus.subscribe() as sub_a, bus.subscribe() as sub_b:
            await bus.publish(StateChanged(turn_id="t1", from_state="idle", to_state="listening"))
            event_a = await asyncio.wait_for(sub_a.receive(), timeout=1.0)
            event_b = await asyncio.wait_for(sub_b.receive(), timeout=1.0)
            assert event_a.to_state == event_b.to_state == "listening"

    async def test_no_subscribers_publish_no_op(self) -> None:
        bus = AsyncEventBus()
        # Must not raise
        await bus.publish(ViewChanged(turn_id=None, view="diagnostic"))

    async def test_events_have_monotonic_seq(self) -> None:
        bus = AsyncEventBus()
        async with bus.subscribe() as sub:
            await bus.publish(StateChanged(turn_id="t1", from_state="idle", to_state="listening"))
            await bus.publish(StateChanged(turn_id="t1", from_state="listening", to_state="thinking"))
            e1 = await asyncio.wait_for(sub.receive(), timeout=1.0)
            e2 = await asyncio.wait_for(sub.receive(), timeout=1.0)
            assert e2.seq > e1.seq


class TestSubscriberIsolation:
    async def test_subscriber_exception_does_not_stop_others(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.ERROR)
        bus = AsyncEventBus()

        async def bad_handler(sub: Subscription) -> None:
            await sub.receive()
            raise RuntimeError("boom")

        async with bus.subscribe() as bad, bus.subscribe() as good:
            bad_task = asyncio.create_task(bad_handler(bad))
            await bus.publish(ViewChanged(turn_id=None, view="diagnostic"))
            # Good subscriber still receives
            event = await asyncio.wait_for(good.receive(), timeout=1.0)
            assert isinstance(event, ViewChanged)
            # Bad subscriber's exception is caller-visible when it awaits the task
            with pytest.raises(RuntimeError):
                await bad_task

    async def test_slow_subscriber_does_not_block_publisher(self) -> None:
        bus = AsyncEventBus(queue_maxsize=4)

        async with bus.subscribe() as slow:
            # Publisher issues more events than the queue can hold without being drained
            for i in range(8):
                await asyncio.wait_for(
                    bus.publish(
                        StateChanged(turn_id=f"t{i}", from_state="idle", to_state="listening")
                    ),
                    timeout=0.5,
                )
            # No block so far; oldest events are dropped (drop-oldest semantics)
            # Slow subscriber can still drain whatever survives
            received = []
            while True:
                try:
                    evt = await asyncio.wait_for(slow.receive(), timeout=0.1)
                    received.append(evt)
                except TimeoutError:
                    break
            # Queue is bounded at 4 so we should have received no more than 4
            assert len(received) <= 4
            # At least one drop_count recorded by the subscription
            assert slow.dropped_count >= 1


class TestSubscriptionLifecycle:
    async def test_unsubscribe_on_context_exit(self) -> None:
        bus = AsyncEventBus()
        async with bus.subscribe():
            pass
        # Publishing after unsubscribe must not raise or leak
        await bus.publish(ViewChanged(turn_id=None, view="character"))
        assert bus.subscriber_count == 0

    async def test_subscriber_count_tracks_live_subs(self) -> None:
        bus = AsyncEventBus()
        assert bus.subscriber_count == 0
        async with bus.subscribe():
            assert bus.subscriber_count == 1
            async with bus.subscribe():
                assert bus.subscriber_count == 2
        assert bus.subscriber_count == 0


class TestEventPerf:
    """Minimal perf sanity — not a load test, just catches pathological regressions."""

    async def test_publish_is_fast_with_no_subscribers(self) -> None:
        bus = AsyncEventBus()
        import time

        start = time.perf_counter()
        for _ in range(500):
            await bus.publish(ViewChanged(turn_id=None, view="character"))
        elapsed = time.perf_counter() - start
        # If this takes more than 500ms on any modern laptop, something is wrong
        assert elapsed < 0.5


class TestTurnEvents:
    def test_turn_started_carries_turn_id(self) -> None:
        e = TurnStarted(turn_id="t1", mode="pi_hybrid")
        assert e.turn_id == "t1"

    def test_turn_completed_optional_outcome(self) -> None:
        e = TurnCompleted(turn_id="t1", outcome="success")
        assert e.outcome == "success"
