"""Secrets loader: ~/.herbert/secrets.env with 0600 perms, fail-closed on missing required keys."""

from __future__ import annotations

import logging
import secrets as _stdlib_secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


class MissingSecretError(RuntimeError):
    """Raised when a required secret is not present in the store."""


@dataclass
class SecretsStore:
    """In-memory view of the secrets file. Values never logged; redact when debugging."""

    path: Path
    _values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def require(self, key: str) -> str:
        value = self._values.get(key)
        if value is None:
            raise MissingSecretError(
                f"Required secret {key!r} missing from {self.path}. "
                f"Add it to the file (permissions 0600) and restart."
            )
        return value

    def set(self, key: str, value: str) -> None:
        self._values[key] = value


def load_secrets(path: Path) -> SecretsStore:
    """Load secrets from a dotenv-style file.

    Missing file is treated as empty store — callers that need specific keys
    should use `.require()` to trigger a fail-closed error.
    """
    store = SecretsStore(path=path)
    if not path.exists():
        return store
    _check_permissions(path)
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            log.warning("ignoring malformed secrets line: %r", stripped)
            continue
        key, _, raw_value = stripped.partition("=")
        value = _unquote(raw_value.strip())
        store._values[key.strip()] = value
    return store


def ensure_frontend_bearer_token(path: Path) -> str:
    """Return the existing FRONTEND_BEARER_TOKEN, generating + persisting one if missing."""
    store = load_secrets(path) if path.exists() else SecretsStore(path=path)
    existing = store.get("FRONTEND_BEARER_TOKEN")
    if existing:
        _enforce_permissions(path)
        return existing
    token = _stdlib_secrets.token_urlsafe(32)
    store.set("FRONTEND_BEARER_TOKEN", token)
    _write_store(store)
    _enforce_permissions(path)
    return token


def _write_store(store: SecretsStore) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={_quote_if_needed(v)}" for k, v in store._values.items()]
    store.path.write_text("\n".join(lines) + "\n")


def _enforce_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError as exc:
        log.warning("could not chmod 0600 on %s: %s", path, exc)


def _check_permissions(path: Path) -> None:
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning(
            "secrets file %s has permission %o (expected 0600); "
            "treating drift as non-fatal but you should tighten it",
            path,
            mode,
        )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _quote_if_needed(value: str) -> str:
    if any(c.isspace() for c in value) or "#" in value:
        return f'"{value}"'
    return value
