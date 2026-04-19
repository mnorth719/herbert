"""StateMachine transition behaviour."""

from __future__ import annotations

import asyncio

from herbert.events import AsyncEventBus, StateChanged
from herbert.state import StateMachine


async def test_initial_state_is_idle() -> None:
    sm = StateMachine(AsyncEventBus())
    assert sm.state == "idle"


async def test_transition_publishes_event() -> None:
    bus = AsyncEventBus()
    sm = StateMachine(bus)
    async with bus.subscribe() as sub:
        fired = await sm.transition("listening", turn_id="t1")
        assert fired is True
        event = await asyncio.wait_for(sub.receive(), timeout=1.0)
        assert isinstance(event, StateChanged)
        assert event.from_state == "idle"
        assert event.to_state == "listening"
        assert event.turn_id == "t1"
    assert sm.state == "listening"


async def test_same_state_is_noop() -> None:
    bus = AsyncEventBus()
    sm = StateMachine(bus, initial="idle")
    async with bus.subscribe() as sub:
        fired = await sm.transition("idle")
        assert fired is False
        await asyncio.sleep(0.01)
        assert sub._queue.empty()


async def test_any_state_to_error_always_allowed() -> None:
    bus = AsyncEventBus()
    sm = StateMachine(bus, initial="speaking")
    assert await sm.transition_to_error(turn_id="t2") is True
    assert sm.state == "error"


async def test_error_recovery_to_listening() -> None:
    bus = AsyncEventBus()
    sm = StateMachine(bus, initial="error")
    assert await sm.transition("listening", turn_id="t3") is True
    assert sm.state == "listening"


async def test_happy_path_sequence() -> None:
    bus = AsyncEventBus()
    sm = StateMachine(bus)
    sequence: list[str] = []
    async with bus.subscribe() as sub:

        async def _listen() -> None:
            for _ in range(4):
                event = await asyncio.wait_for(sub.receive(), timeout=1.0)
                assert isinstance(event, StateChanged)
                sequence.append(f"{event.from_state}->{event.to_state}")

        listener = asyncio.create_task(_listen())
        await sm.transition("listening")
        await sm.transition("thinking")
        await sm.transition("speaking")
        await sm.transition("idle")
        await listener

    assert sequence == [
        "idle->listening",
        "listening->thinking",
        "thinking->speaking",
        "speaking->idle",
    ]
