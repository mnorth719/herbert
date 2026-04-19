"""FastAPI app + WS endpoint tests via the in-process TestClient.

No uvicorn thread spin-up here; we exercise the app directly. The
thread/janus bridge is covered in test_web_server_thread.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from herbert.web.app import create_app
from herbert.web.auth import AuthConfig
from herbert.web.ws import Broadcaster


def _mk_app(*, expose: bool, token: str | None = None):  # type: ignore[no-untyped-def]
    auth = AuthConfig(expose=expose, bearer_token=token)
    broadcaster = Broadcaster()
    app = create_app(
        auth=auth,
        broadcaster=broadcaster,
        health_provider=lambda: {"status": "ok", "state": "idle"},
    )
    return app, broadcaster


class TestHealthz:
    def test_localhost_no_auth_required(self) -> None:
        app, _ = _mk_app(expose=False)
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["state"] == "idle"

    def test_exposed_requires_bearer(self) -> None:
        app, _ = _mk_app(expose=True, token="secret")
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 401

    def test_exposed_accepts_header_token(self) -> None:
        app, _ = _mk_app(expose=True, token="secret")
        client = TestClient(app)
        resp = client.get("/healthz", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200

    def test_exposed_accepts_query_token(self) -> None:
        app, _ = _mk_app(expose=True, token="secret")
        client = TestClient(app)
        resp = client.get("/healthz?token=secret")
        assert resp.status_code == 200

    def test_exposed_rejects_wrong_token(self) -> None:
        app, _ = _mk_app(expose=True, token="secret")
        client = TestClient(app)
        resp = client.get("/healthz", headers={"Authorization": "Bearer WRONG"})
        assert resp.status_code == 401


class TestWebSocket:
    def test_localhost_ws_connects_without_token(self) -> None:
        app, broadcaster = _mk_app(expose=False)
        client = TestClient(app)
        with client.websocket_connect("/ws"):
            assert broadcaster.client_count == 1
        # Context exit unregisters
        assert broadcaster.client_count == 0

    def test_exposed_ws_rejects_missing_token(self) -> None:
        app, _ = _mk_app(expose=True, token="secret")
        client = TestClient(app)
        with pytest.raises(Exception):  # noqa: B017
            with client.websocket_connect("/ws"):
                pass

    def test_exposed_ws_accepts_query_token(self) -> None:
        app, broadcaster = _mk_app(expose=True, token="secret")
        client = TestClient(app)
        with client.websocket_connect("/ws?token=secret"):
            assert broadcaster.client_count == 1
        assert broadcaster.client_count == 0
