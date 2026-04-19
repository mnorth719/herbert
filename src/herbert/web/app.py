"""FastAPI app factory.

Routes:
  GET  /healthz           → JSON with current state + provider names
  GET  /                  → static mount (frontend build output, mounted if present)
  WS   /ws                → state-event broadcast channel

Auth: localhost bind skips auth entirely; --expose enforces a bearer
token on every route (see `auth.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from herbert.web.auth import AuthConfig, make_http_dependency, verify_websocket
from herbert.web.ws import Broadcaster, websocket_loop

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    *,
    auth: AuthConfig,
    broadcaster: Broadcaster,
    health_provider,  # type: ignore[no-untyped-def]
    snapshot_accessor=None,  # type: ignore[no-untyped-def]
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """Build the FastAPI instance. Dependencies are injected by the server factory.

    `snapshot_accessor` is a zero-arg callable that returns the current
    snapshot provider (another callable) — or None if not yet configured.
    This double-indirection lets the daemon register/replace the provider
    *after* the web thread has started. /api/boot_snapshot reads through
    it on every request, so persona hot-reloads show up immediately.
    """
    app = FastAPI(
        title="Herbert",
        docs_url=None,  # no need for swagger on a single-user device
        redoc_url=None,
    )
    http_auth = make_http_dependency(auth)

    @app.get("/healthz")
    async def healthz(_auth: None = Depends(http_auth)) -> JSONResponse:
        payload: dict[str, Any] = health_provider()
        return JSONResponse(payload)

    @app.get("/api/boot_snapshot")
    async def boot_snapshot(_auth: None = Depends(http_auth)) -> JSONResponse:
        accessor = snapshot_accessor
        provider = accessor() if accessor is not None else None
        if provider is None:
            return JSONResponse(
                {"error": "snapshot provider not yet configured"},
                status_code=503,
            )
        try:
            return JSONResponse(provider())
        except Exception as exc:
            log.warning("boot snapshot provider raised: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        if not await verify_websocket(ws, auth):
            await ws.close(code=4401)
            return
        await ws.accept()
        await websocket_loop(ws, broadcaster)

    if static_dir.exists() and any(static_dir.iterdir()):
        # Mounted only when the frontend build has deposited assets. Without
        # this guard, an empty dir still works but returns 404s everywhere.
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
