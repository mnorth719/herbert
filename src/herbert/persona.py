"""Persona loader with hot-reload and last-good-cached fallback.

Matt can edit `~/.herbert/persona.md` while the daemon runs and the next
turn will pick up the change automatically. The behaviour rules:

  - First read happens at daemon startup. If the configured persona file
    exists but is empty/unreadable, fail loudly — Matt set it up wrong,
    tell him now rather than letting him debug a puzzling response later.
  - If the file doesn't exist, fall back to `DEFAULT_PERSONA` (the
    built-in constant from `herbert.daemon`). Reading from disk is skipped
    until the file appears.
  - After first load, every `get_current()` call checks mtime. If the file
    has been touched since last load, reread it. If the reread fails
    (disappeared, unreadable, empty), keep the *last good* cached content
    and log a WARN — runtime failures of the persona file must not brick
    Herbert mid-conversation.
  - `DEFAULT_PERSONA` never appears if a file was ever successfully loaded;
    the cache persists for the daemon's lifetime.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class PersonaMissingError(RuntimeError):
    """Raised when the persona file exists but is unreadable or empty at startup."""


class PersonaCache:
    """mtime-tracked persona loader. Thread-safe for the daemon's use."""

    def __init__(self, path: Path, default: str) -> None:
        self._path = path
        self._default = default
        self._cached: str | None = None
        self._cached_mtime: float | None = None
        self._lock = threading.Lock()

    def prime_at_startup(self) -> None:
        """Attempt an initial load. If the file exists but is unreadable/empty,
        raise `PersonaMissingError`. If the file doesn't exist, stay on the
        default — that's fine, Matt just hasn't customised yet."""
        if not self._path.exists():
            log.info("persona file not found at %s — using built-in default", self._path)
            return
        try:
            content = self._path.read_text()
        except OSError as exc:
            raise PersonaMissingError(
                f"persona file exists at {self._path} but is unreadable: {exc}"
            ) from exc
        if not content.strip():
            raise PersonaMissingError(
                f"persona file at {self._path} is empty (delete it to use the default)"
            )
        self._cached = content
        self._cached_mtime = self._path.stat().st_mtime
        log.info("persona loaded from %s (%d bytes)", self._path, len(content))

    def get_current(self) -> str:
        """Return the active persona text. Cheap in the common case (mtime
        unchanged), performs one file read when the file has been touched."""
        with self._lock:
            self._maybe_reload_locked()
            return self._cached if self._cached is not None else self._default

    def _maybe_reload_locked(self) -> None:
        if not self._path.exists():
            # File was deleted after startup — keep the cache, don't fall
            # back to the default mid-conversation.
            if self._cached is not None:
                log.warning("persona file disappeared at %s; keeping last-good cache", self._path)
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if self._cached_mtime == mtime:
            return
        try:
            content = self._path.read_text()
        except OSError as exc:
            log.warning("persona reread failed: %s; keeping last-good cache", exc)
            return
        if not content.strip():
            log.warning("persona file went empty at %s; keeping last-good cache", self._path)
            return
        # Fresh successful load
        was = "reloaded" if self._cached is not None else "loaded"
        self._cached = content
        self._cached_mtime = mtime
        log.info("persona %s from %s (%d bytes)", was, self._path, len(content))
