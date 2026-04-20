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


def _mk_app_with_prompt_provider(
    *,
    provider,  # type: ignore[no-untyped-def]
    expose: bool = False,
    token: str | None = None,
):  # type: ignore[no-untyped-def]
    """Build an app with a registered prompt snapshot provider for the
    /api/prompt/snapshot tests."""
    auth = AuthConfig(expose=expose, bearer_token=token)
    broadcaster = Broadcaster()
    app = create_app(
        auth=auth,
        broadcaster=broadcaster,
        health_provider=lambda: {"status": "ok", "state": "idle"},
        prompt_snapshot_accessor=lambda: provider,
    )
    return app


class TestPromptSnapshotEndpoint:
    def test_returns_503_when_provider_not_set(self) -> None:
        app = _mk_app_with_prompt_provider(provider=None)
        client = TestClient(app)
        resp = client.get("/api/prompt/snapshot")
        assert resp.status_code == 503
        assert "not yet configured" in resp.json()["error"]

    def test_returns_200_with_full_shape(self) -> None:
        provider = lambda: {  # noqa: E731
            "persona": {"text": "YOU ARE HERBERT", "tokens": 4},
            "tools_addendum": {"text": "...tools...", "tokens": 3},
            "facts": {"items": ["Matt lives in Monrovia"], "tokens": 6},
            "summaries": {
                "items": [{"date": "Thu Apr 18", "summary": "introductions"}],
                "tokens": 4,
            },
            "live_messages": [{"role": "user", "content": "what's my name?"}],
            "live_messages_tokens": 5,
            "total_tokens": 22,
            "memory_enabled": True,
        }
        app = _mk_app_with_prompt_provider(provider=provider)
        client = TestClient(app)
        resp = client.get("/api/prompt/snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["persona"]["text"] == "YOU ARE HERBERT"
        assert body["facts"]["items"] == ["Matt lives in Monrovia"]
        assert body["summaries"]["items"][0]["summary"] == "introductions"
        assert body["live_messages"][0]["role"] == "user"
        assert body["memory_enabled"] is True

    def test_memory_disabled_shape(self) -> None:
        provider = lambda: {  # noqa: E731
            "persona": {"text": "YOU ARE HERBERT", "tokens": 4},
            "tools_addendum": None,
            "facts": {"items": [], "tokens": 0},
            "summaries": {"items": [], "tokens": 0},
            "live_messages": [],
            "live_messages_tokens": 0,
            "total_tokens": 4,
            "memory_enabled": False,
        }
        app = _mk_app_with_prompt_provider(provider=provider)
        client = TestClient(app)
        resp = client.get("/api/prompt/snapshot")
        assert resp.status_code == 200
        body = resp.json()
        assert body["memory_enabled"] is False
        assert body["facts"]["items"] == []
        assert body["tools_addendum"] is None

    def test_provider_exception_returns_500(self) -> None:
        def _bad() -> dict:
            raise RuntimeError("boom")

        app = _mk_app_with_prompt_provider(provider=_bad)
        client = TestClient(app)
        resp = client.get("/api/prompt/snapshot")
        assert resp.status_code == 500
        assert "boom" in resp.json()["error"]

    def test_exposed_requires_bearer(self) -> None:
        provider = lambda: {  # noqa: E731
            "persona": {"text": "x", "tokens": 1},
            "tools_addendum": None,
            "facts": {"items": [], "tokens": 0},
            "summaries": {"items": [], "tokens": 0},
            "live_messages": [],
            "live_messages_tokens": 0,
            "total_tokens": 1,
            "memory_enabled": False,
        }
        app = _mk_app_with_prompt_provider(provider=provider, expose=True, token="secret")
        client = TestClient(app)
        resp = client.get("/api/prompt/snapshot")
        assert resp.status_code == 401
        resp = client.get("/api/prompt/snapshot", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200
