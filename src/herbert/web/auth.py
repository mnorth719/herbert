"""Bearer-token dependency for the HTTP + WebSocket surface.

Trust model
-----------
* Localhost (bind 127.0.0.1) is UNAUTHENTICATED by design: any process on
  the device is already trusted per the single-user-home-device threat
  model. The dependency returns early when `expose` is False.
* Exposed mode (bind 0.0.0.0 via --expose) enforces a bearer token on
  every WS + HTTP route. The token is generated on first boot and lives
  in ~/.herbert/secrets.env (see `secrets.ensure_frontend_bearer_token`).

Token arrival shape
-------------------
* Preferred: `Authorization: Bearer <token>` header.
* Fallback: `?token=<token>` query string (for QR-scan convenience). The
  RedactingFilter (see logging.py) scrubs tokens from URL query params in
  uvicorn's access logs so the token-in-URL posture doesn't leak through
  the file sink.

Comparison is constant-time (`hmac.compare_digest`) to defeat timing
side-channels.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, Query, WebSocket, status

log = logging.getLogger(__name__)


class AuthConfig:
    """Injected by the server factory; drives the auth dependencies."""

    def __init__(self, *, expose: bool, bearer_token: str | None) -> None:
        self.expose = expose
        self.bearer_token = bearer_token

    def verify(self, supplied: str | None) -> bool:
        if not self.expose:
            return True
        if not self.bearer_token or not supplied:
            return False
        return hmac.compare_digest(self.bearer_token, supplied)


def _extract_bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def make_http_dependency(auth: AuthConfig):  # type: ignore[no-untyped-def]
    """Build a FastAPI dependency that enforces bearer auth on HTTP routes."""

    async def _http_auth(
        authorization: str | None = Header(default=None),
        token: str | None = Query(default=None),
    ) -> None:
        if not auth.expose:
            return
        supplied = _extract_bearer(authorization) or token
        if not auth.verify(supplied):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return _http_auth


async def verify_websocket(
    ws: WebSocket,
    auth: AuthConfig,
) -> bool:
    """Enforce bearer auth on an incoming WS before `accept()`.

    Returns True if the client is allowed. On False, the caller should
    call `ws.close(code=4401)` without accepting.
    """
    if not auth.expose:
        return True
    header = ws.headers.get("authorization")
    token_param = ws.query_params.get("token")
    supplied = _extract_bearer(header) or token_param
    if not auth.verify(supplied):
        log.warning("ws: rejecting client with invalid bearer (client=%s)", ws.client)
        return False
    return True
