"""Broadcaster behavior — client register/unregister + fan-out."""

from __future__ import annotations

import asyncio

from herbert.events import StateChanged
from herbert.web.ws import Broadcaster


class _FakeWebSocket:
    """Minimal WS double that records sent payloads."""

    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.sent: list[dict] = []
        self._fail = fail_on_send

    async def send_json(self, payload: dict) -> None:
        if self._fail:
            raise RuntimeError("client went away")
        self.sent.append(payload)


async def test_register_and_broadcast_to_single_client() -> None:
    b = Broadcaster()
    ws = _FakeWebSocket()
    await b.register(ws)  # type: ignore[arg-type]
    evt = StateChanged(turn_id="t1", from_state="idle", to_state="listening")
    await b.broadcast(evt)
    assert len(ws.sent) == 1
    assert ws.sent[0]["event_type"] == "state_changed"
    assert ws.sent[0]["to_state"] == "listening"


async def test_broadcast_to_multiple_clients() -> None:
    b = Broadcaster()
    clients = [_FakeWebSocket() for _ in range(3)]
    for c in clients:
        await b.register(c)  # type: ignore[arg-type]
    evt = StateChanged(turn_id="t2", from_state="idle", to_state="listening")
    await b.broadcast(evt)
    for c in clients:
        assert len(c.sent) == 1


async def test_unregister_stops_delivery() -> None:
    b = Broadcaster()
    a, drop = _FakeWebSocket(), _FakeWebSocket()
    await b.register(a)  # type: ignore[arg-type]
    await b.register(drop)  # type: ignore[arg-type]
    await b.unregister(drop)  # type: ignore[arg-type]
    await b.broadcast(StateChanged(turn_id="t3", from_state="idle", to_state="listening"))
    assert len(a.sent) == 1
    assert len(drop.sent) == 0


async def test_failing_client_gets_dropped() -> None:
    b = Broadcaster()
    good = _FakeWebSocket()
    bad = _FakeWebSocket(fail_on_send=True)
    await b.register(good)  # type: ignore[arg-type]
    await b.register(bad)  # type: ignore[arg-type]
    assert b.client_count == 2

    await b.broadcast(StateChanged(turn_id="t4", from_state="idle", to_state="listening"))
    assert len(good.sent) == 1
    # Bad client has been evicted after the failure
    assert b.client_count == 1


async def test_broadcast_during_register_does_not_deadlock() -> None:
    b = Broadcaster()

    async def _register_many() -> None:
        for _ in range(10):
            await b.register(_FakeWebSocket())  # type: ignore[arg-type]

    async def _broadcast_many() -> None:
        for i in range(10):
            await b.broadcast(
                StateChanged(turn_id=f"t{i}", from_state="idle", to_state="listening")
            )

    await asyncio.gather(_register_many(), _broadcast_many())
    assert b.client_count == 10
