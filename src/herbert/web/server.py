"""Run uvicorn in a dedicated thread + event loop.

Rationale (plan Unit 8 decision): an SSE log-tail request serialising
100MB of rotated log shouldn't delay PortAudio's `call_soon_threadsafe`
callbacks. Isolating uvicorn in its own thread keeps the audio path
jitter-free while still sharing state cleanly via `janus.Queue`.

Lifecycle:
  start()                 → spawns thread, blocks until web loop has
                            created its queue/broadcaster + uvicorn is
                            running. Safe to call `send_event()` after.
  send_event(evt)         → daemon-side push (sync_q.put_nowait).
  stop(timeout=3.0)       → signals uvicorn to exit + joins the thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

import janus
import uvicorn
from pydantic import BaseModel

from herbert.web.app import create_app
from herbert.web.auth import AuthConfig
from herbert.web.ws import Broadcaster

log = logging.getLogger(__name__)


class WebServer:
    """Uvicorn + broadcaster running on a side thread."""

    def __init__(
        self,
        *,
        bind_host: str = "127.0.0.1",
        port: int = 8080,
        expose: bool = False,
        bearer_token: str | None = None,
        health_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._bind_host = bind_host
        self._port = port
        self._auth = AuthConfig(expose=expose, bearer_token=bearer_token)
        self._health_provider = health_provider or (lambda: {"status": "ok"})
        # Late-bound snapshot provider. The daemon sets this after it
        # finishes wiring up persona + tools so /api/boot_snapshot can
        # return current state. Until set, the endpoint returns 503.
        self._snapshot_provider: Callable[[], dict[str, Any]] | None = None
        self._queue: janus.Queue[BaseModel] | None = None
        self._broadcaster: Broadcaster | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()

    # --- Public API -----------------------------------------------------

    def start(self, *, ready_timeout: float = 5.0) -> None:
        """Spawn the thread + block until the server is accepting connections."""
        self._thread = threading.Thread(
            target=self._thread_main,
            name="herbert-web",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=ready_timeout):
            raise RuntimeError("web thread failed to come up within timeout")

    def stop(self, *, timeout: float = 3.0) -> None:
        """Signal shutdown and join the thread. Idempotent."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self._server is not None:
            self._server.should_exit = True
        if self._queue is not None:
            # Unblock the fanout coroutine if it's sitting on queue.get()
            try:
                self._queue.sync_q.put_nowait(_SENTINEL)  # type: ignore[arg-type]
            except janus.SyncQueueFull:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def send_event(self, event: BaseModel) -> None:
        """Daemon-side: push an event onto the cross-thread queue.

        Safe to call from any thread. Drops (with WARN) when the queue is
        full — a full queue means the web side is backed up, and a stuck
        WS client must not starve the audio path.
        """
        if self._queue is None:
            return
        try:
            self._queue.sync_q.put_nowait(event)
        except janus.SyncQueueFull:
            log.warning("web queue full; dropping %s", type(event).__name__)

    @property
    def url(self) -> str:
        return f"http://{self._bind_host}:{self._port}"

    # --- Thread internals ----------------------------------------------

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception:
            log.exception("web thread crashed")
            self._ready.set()  # unblock start() so caller sees failure

    async def _async_main(self) -> None:
        self._queue = janus.Queue(maxsize=1024)
        self._broadcaster = Broadcaster()

        app = create_app(
            auth=self._auth,
            broadcaster=self._broadcaster,
            health_provider=self._health_provider,
            snapshot_accessor=lambda: self._snapshot_provider,
        )
        config = uvicorn.Config(
            app,
            host=self._bind_host,
            port=self._port,
            log_level="warning",
            access_log=False,
            # Clean shutdown when server.should_exit=True is set from main thread
            timeout_graceful_shutdown=2,
        )
        self._server = uvicorn.Server(config)

        serve_task = asyncio.create_task(self._server.serve())
        fanout_task = asyncio.create_task(self._fanout())

        # Wait until uvicorn has bound the port before unblocking start()
        while not self._server.started and not self._stopped.is_set():
            await asyncio.sleep(0.01)
        self._ready.set()

        try:
            await serve_task
        finally:
            fanout_task.cancel()
            try:
                await fanout_task
            except asyncio.CancelledError:
                pass
            await self._queue.aclose()

    async def _fanout(self) -> None:
        assert self._queue is not None and self._broadcaster is not None
        while True:
            event = await self._queue.async_q.get()
            if event is _SENTINEL:
                return
            try:
                await self._broadcaster.broadcast(event)
            except Exception as exc:
                log.warning("broadcast failed: %s", exc)


# Sentinel object pushed into the queue on stop() to unblock the fanout
_SENTINEL: Any = object()
