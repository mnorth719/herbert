"""MemoryStore: queue-backed writes + direct reads against SQLite.

The store owns two connections and one thread.

  Writes go on ``self._queue`` as ``(op, args)`` tuples. A daemon thread
  drains the queue and applies each op against the writer connection.
  Callers never touch SQLite directly on the write path, so the event
  loop is never blocked by disk I/O — even under SD-card spikes on the
  Pi. IDs (``session_id``, ``turn_id``) are generated in-caller using
  ULIDs so that ``append_turn`` can return a usable id synchronously
  while the INSERT still sits in the queue.

  Reads happen on whatever thread the caller is already on, against a
  shared reader connection opened with ``check_same_thread=False``. A
  small lock serialises access to prevent cursor-reuse surprises when
  two threads hit a read method at the same time. WAL mode lets readers
  and the writer run concurrently with no extra coordination.

Shutdown is deterministic: ``close()`` enqueues a sentinel, joins the
writer thread (with a 2 s ceiling), and closes both connections. The
``drain()`` helper exists for tests that want to wait for the current
backlog to land before asserting.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import ulid

from herbert.memory.db import open_reader_connection, open_writer_connection

log = logging.getLogger(__name__)


# Sentinel that tells the writer loop to exit cleanly.
_SHUTDOWN = object()

# Queue-drain ceiling on shutdown. Longer than the 99th-percentile drain
# on a loaded Pi, shorter than a user's patience for Ctrl-C.
_SHUTDOWN_TIMEOUT_S = 2.0


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._writer_conn = open_writer_connection(db_path)
        self._reader_conn = open_reader_connection(db_path)
        self._reader_lock = threading.Lock()

        self._queue: queue.Queue[Any] = queue.Queue()
        self._closed = threading.Event()
        # Event that tests can use to wait for an empty queue + idle
        # writer. Set whenever the writer finishes an op AND the queue is
        # empty; cleared on each new enqueue.
        self._idle = threading.Event()
        self._idle.set()

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="herbert-memory-writer",
            daemon=True,
        )
        self._writer_thread.start()

    # ------------------------------------------------------------------
    # Public write API — returns synchronously, enqueues to writer thread
    # ------------------------------------------------------------------

    def start_session(self) -> str:
        session_id = _new_ulid()
        started_at = int(time.time())
        self._enqueue(("INSERT_SESSION", session_id, started_at))
        return session_id

    def append_turn(self, session_id: str, role: str, content: str) -> str:
        turn_id = _new_ulid()
        ts = int(time.time())
        self._enqueue(("INSERT_TURN", turn_id, session_id, ts, role, content))
        return turn_id

    def pop_turn(self, turn_id: str) -> None:
        self._enqueue(("DELETE_TURN", turn_id))

    def replace_turn(self, turn_id: str, role: str, content: str) -> None:
        self._enqueue(("UPDATE_TURN", turn_id, role, content))

    def close_session(
        self,
        session_id: str,
        summary: str | None,
        new_facts: list[str],
    ) -> None:
        ended_at = int(time.time())
        self._enqueue(
            ("CLOSE_SESSION", session_id, ended_at, summary, list(new_facts))
        )

    # ------------------------------------------------------------------
    # Public read API — direct against the reader connection
    # ------------------------------------------------------------------

    def get_facts(self) -> list[str]:
        with self._reader_lock:
            rows = self._reader_conn.execute(
                "SELECT content FROM facts ORDER BY fact_id ASC"
            ).fetchall()
        return [r[0] for r in rows]

    def get_recent_summaries(self, n: int) -> list[tuple[str, int]]:
        with self._reader_lock:
            rows = self._reader_conn.execute(
                "SELECT summary, ended_at FROM sessions "
                "WHERE summary IS NOT NULL "
                "ORDER BY ended_at DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def get_session_turns(self, session_id: str) -> list[tuple[str, str]]:
        """Return ``(role, content)`` pairs for a session, oldest first.

        Ordering is by SQLite's implicit ``rowid`` (insertion order).
        ``ts`` granularity is 1 second and two turns often land inside
        the same second; ULIDs aren't guaranteed monotonic within a
        second either. ``rowid`` is the only column that preserves the
        exact order ``append_turn`` was called.

        Returning plain tuples (rather than ``Message`` objects from
        ``herbert.session``) keeps this module free of a circular import.
        """
        with self._reader_lock:
            rows = self._reader_conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id=? ORDER BY rowid ASC",
                (session_id,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(_SHUTDOWN)
        self._writer_thread.join(timeout=_SHUTDOWN_TIMEOUT_S)
        if self._writer_thread.is_alive():
            log.warning(
                "memory writer did not finish within %.1fs; %d op(s) may be lost",
                _SHUTDOWN_TIMEOUT_S,
                self._queue.qsize(),
            )
        try:
            self._writer_conn.close()
        except Exception as exc:
            log.warning("writer connection close failed: %s", exc)
        try:
            self._reader_conn.close()
        except Exception as exc:
            log.warning("reader connection close failed: %s", exc)

    def drain(self, timeout: float = 2.0) -> None:
        """Block until all queued ops have been applied. Tests use this
        to avoid having to pepper assertions with sleeps."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.empty() and self._idle.is_set():
                return
            self._idle.wait(timeout=0.05)
        log.warning("memory drain timed out; %d op(s) still queued", self._queue.qsize())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enqueue(self, op: tuple[Any, ...]) -> None:
        self._idle.clear()
        self._queue.put(op)

    def _writer_loop(self) -> None:
        conn = self._writer_conn
        while True:
            op = self._queue.get()
            if op is _SHUTDOWN:
                return
            try:
                self._apply(conn, op)
            except Exception as exc:
                # Never let one bad op kill the writer loop — log and
                # keep draining so subsequent legitimate writes still land.
                log.warning("memory writer op %s failed: %s", op[0], exc)
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    @staticmethod
    def _apply(conn: sqlite3.Connection, op: tuple[Any, ...]) -> None:
        kind = op[0]
        if kind == "INSERT_SESSION":
            _, session_id, started_at = op
            conn.execute(
                "INSERT INTO sessions (session_id, started_at) VALUES (?, ?)",
                (session_id, started_at),
            )
            conn.commit()
            return
        if kind == "INSERT_TURN":
            _, turn_id, session_id, ts, role, content = op
            conn.execute(
                "INSERT INTO messages (turn_id, session_id, ts, role, content)"
                " VALUES (?, ?, ?, ?, ?)",
                (turn_id, session_id, ts, role, content),
            )
            conn.commit()
            return
        if kind == "DELETE_TURN":
            _, turn_id = op
            conn.execute("DELETE FROM messages WHERE turn_id = ?", (turn_id,))
            conn.commit()
            return
        if kind == "UPDATE_TURN":
            _, turn_id, role, content = op
            conn.execute(
                "UPDATE messages SET role=?, content=? WHERE turn_id=?",
                (role, content, turn_id),
            )
            conn.commit()
            return
        if kind == "CLOSE_SESSION":
            _, session_id, ended_at, summary, new_facts = op
            # Atomic across the session seal and every fact insert. On
            # failure, the single transaction rolls back cleanly.
            conn.execute("BEGIN")
            try:
                conn.execute(
                    "UPDATE sessions SET ended_at=?, summary=? WHERE session_id=?",
                    (ended_at, summary, session_id),
                )
                for fact in new_facts:
                    _add_fact(conn, fact, source_session=session_id, now=ended_at)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return
        log.warning("memory writer received unknown op %r", kind)

    # ------------------------------------------------------------------
    # Test-only peek
    # ------------------------------------------------------------------

    def _reader_execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple]:
        """Escape hatch for tests that need to inspect state without a
        dedicated public accessor (e.g. last_confirmed bumps)."""
        with self._reader_lock:
            return self._reader_conn.execute(sql, params).fetchall()


def _new_ulid() -> str:
    """Monotonic-enough id. ``ulid-py`` is already a project dep."""
    return ulid.new().str


def _add_fact(
    conn: sqlite3.Connection,
    content: str,
    *,
    source_session: str,
    now: int,
) -> None:
    """Insert a fact or bump its ``last_confirmed`` if it already exists.

    UNIQUE(content) means ``INSERT OR IGNORE`` handles dedup; we then
    issue an UPDATE to touch ``last_confirmed`` on the existing row. One
    net round-trip either way (INSERT that lands + UPDATE that matches
    zero rows, or IGNORE that does nothing + UPDATE that matches one).
    """
    conn.execute(
        "INSERT OR IGNORE INTO facts "
        "(content, source_session, first_seen, last_confirmed) VALUES (?, ?, ?, ?)",
        (content, source_session, now, now),
    )
    conn.execute(
        "UPDATE facts SET last_confirmed=? WHERE content=?",
        (now, content),
    )
