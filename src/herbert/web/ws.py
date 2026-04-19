"""WebSocket broadcaster — fans typed events out to every connected client.

The broadcaster lives in the web thread's asyncio loop. The daemon (in
its own loop on another thread) hands it events via a janus.Queue; the
broadcaster drains the queue and pushes JSON to every active client. A
slow client never blocks the queue: if the client's send backs up, we
drop them and log.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

log = logging.getLogger(__name__)


class Broadcaster:
    """Maintains the WS client set and fans events out JSON-serialised."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: BaseModel | dict[str, Any]) -> None:
        payload = event.model_dump(mode="json") if isinstance(event, BaseModel) else event
        # Snapshot the client set so mutations during iteration don't trip us up
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(payload)
            except (WebSocketDisconnect, RuntimeError) as exc:
                log.info("ws: client dropped during broadcast (%s)", exc)
                dead.append(client)
            except Exception as exc:
                log.warning("ws: send failed, dropping client (%s)", exc)
                dead.append(client)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


async def websocket_loop(ws: WebSocket, broadcaster: Broadcaster) -> None:
    """Run the server-side of one WS connection.

    The daemon is the sole publisher; clients are pure consumers in v1.
    We still read from the socket to detect disconnect promptly.
    """
    await broadcaster.register(ws)
    try:
        while True:
            # Any message from the client means "still here"; we ignore content
            await ws.receive_text()
    except WebSocketDisconnect:
        return
    finally:
        await broadcaster.unregister(ws)
