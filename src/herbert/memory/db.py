"""SQLite connection openers + schema migration.

Two connection flavours — a writer pinned to a single thread (owned by
``MemoryStore``'s writer thread) and a reader that can be used from any
thread (web server, event loop, extractor task). SQLite WAL mode gives
concurrent reader + single-writer semantics without explicit locking,
which is exactly what the two-connection split relies on.

Schema is v1: three tables (``messages``, ``sessions``, ``facts``) plus
two indexes. A ``schema_version`` row tracks upgrades so v2 can alter
the DB in-place when the FTS5 virtual table lands.

Budget: opening + migrating on a small SQLite file is <100ms on every
target platform. The daemon pays this once at boot (R9 in the plan).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def open_writer_connection(path: Path) -> sqlite3.Connection:
    """Open the writer-side connection.

    Creates the parent directory if needed, enables WAL + NORMAL sync +
    foreign keys, and runs ``migrate`` before returning.

    ``check_same_thread=False`` is on because ``MemoryStore`` opens this
    on the main thread during ``__init__`` (to fail fast on schema
    errors) and hands it off to the writer thread for actual use. Only
    the writer thread touches the connection after hand-off; discipline
    is enforced by code structure, not by sqlite3's thread check.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    _configure(conn)
    migrate(conn)
    return conn


def open_reader_connection(path: Path) -> sqlite3.Connection:
    """Open a reader-side connection usable from any thread.

    ``check_same_thread=False`` is how the web thread, the event loop,
    and the extractor task can all issue SELECTs through one shared
    connection while the writer thread handles all mutations. SQLite's
    WAL journal mode guarantees readers see a consistent snapshot and
    never block the writer.

    Skips ``migrate`` — the writer already ran it at startup.
    """
    if not path.exists():
        # Match the writer's parent-dir creation behaviour so tests can
        # open a reader against a path whose parent exists but the file
        # doesn't. In production the writer always opens first.
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    _configure(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Run idempotent schema migration up to ``SCHEMA_VERSION``.

    Uses ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS``
    so re-running on an already-migrated DB is a no-op. The
    ``schema_version`` table tracks the applied version; a stored value
    equal to ``SCHEMA_VERSION`` short-circuits the rest of the work.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version INTEGER PRIMARY KEY"
        ")"
    )
    current = conn.execute("SELECT version FROM schema_version").fetchone()
    if current is not None and current[0] == SCHEMA_VERSION:
        return

    conn.execute(
        "CREATE TABLE IF NOT EXISTS messages ("
        "  turn_id    TEXT PRIMARY KEY,"
        "  session_id TEXT NOT NULL,"
        "  ts         INTEGER NOT NULL,"
        "  role       TEXT NOT NULL,"
        "  content    TEXT NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "  session_id TEXT PRIMARY KEY,"
        "  started_at INTEGER NOT NULL,"
        "  ended_at   INTEGER,"
        "  summary    TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS facts ("
        "  fact_id        INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  content        TEXT NOT NULL UNIQUE,"
        "  source_session TEXT,"
        "  first_seen     INTEGER NOT NULL,"
        "  last_confirmed INTEGER NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_session "
        "ON messages(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_ts "
        "ON messages(ts)"
    )

    if current is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        log.info("memory DB migrated to schema version %d (new file)", SCHEMA_VERSION)
    else:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        log.info(
            "memory DB migrated %d -> %d", current[0], SCHEMA_VERSION
        )
    conn.commit()


def _configure(conn: sqlite3.Connection) -> None:
    """Apply the pragmas every Herbert connection wants."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
