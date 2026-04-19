"""Full WebServer thread + janus bridge end-to-end.

Spawns a real uvicorn on an ephemeral port, sends an event via the
cross-thread `send_event()`, connects a real `websockets` client, and
verifies the payload arrives with the expected shape.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import closing

import httpx
import pytest
import websockets

from herbert.events import StateChanged
from herbert.web.server import WebServer


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_healthz_reachable_on_loopback() -> None:
    port = _free_port()
    server = WebServer(
        bind_host="127.0.0.1",
        port=port,
        expose=False,
        health_provider=lambda: {"status": "ok", "state": "idle"},
    )
    server.start()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/healthz")
            assert resp.status_code == 200
            assert resp.json()["state"] == "idle"
    finally:
        server.stop()


async def test_ws_receives_event_from_daemon_thread() -> None:
    port = _free_port()
    server = WebServer(bind_host="127.0.0.1", port=port, expose=False)
    server.start()
    try:
        uri = f"ws://127.0.0.1:{port}/ws"
        async with websockets.connect(uri) as ws:
            # From the "daemon thread" (here, the test's main thread) push an event
            server.send_event(
                StateChanged(turn_id="t1", from_state="idle", to_state="listening")
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            import json

            payload = json.loads(raw)
            assert payload["event_type"] == "state_changed"
            assert payload["to_state"] == "listening"
    finally:
        server.stop()


async def test_exposed_rejects_ws_without_token() -> None:
    port = _free_port()
    server = WebServer(
        bind_host="127.0.0.1", port=port, expose=True, bearer_token="secret"
    )
    server.start()
    try:
        uri = f"ws://127.0.0.1:{port}/ws"
        with pytest.raises(websockets.InvalidStatus):
            async with websockets.connect(uri):
                pass
    finally:
        server.stop()


async def test_exposed_accepts_ws_with_token_header() -> None:
    port = _free_port()
    server = WebServer(
        bind_host="127.0.0.1", port=port, expose=True, bearer_token="secret"
    )
    server.start()
    try:
        uri = f"ws://127.0.0.1:{port}/ws"
        async with websockets.connect(
            uri, additional_headers={"Authorization": "Bearer secret"}
        ):
            server.send_event(
                StateChanged(turn_id="t1", from_state="idle", to_state="listening")
            )
    finally:
        server.stop()


async def test_exposed_healthz_requires_token() -> None:
    port = _free_port()
    server = WebServer(
        bind_host="127.0.0.1", port=port, expose=True, bearer_token="secret"
    )
    server.start()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/healthz")
            assert resp.status_code == 401
            resp = await client.get(
                f"http://127.0.0.1:{port}/healthz",
                headers={"Authorization": "Bearer secret"},
            )
            assert resp.status_code == 200
    finally:
        server.stop()
