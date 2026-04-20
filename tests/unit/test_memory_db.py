"""Memory DB: connection openers, schema migration, WAL behaviour."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from herbert.memory.db import migrate, open_reader_connection, open_writer_connection


def _list_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


def _list_indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


class TestOpenWriterConnection:
    def test_creates_file_and_parent_dir(self, tmp_path: Path) -> None:
        db_path = tmp_path / "nested" / "deeper" / "memory.db"
        assert not db_path.parent.exists()
        conn = open_writer_connection(db_path)
        try:
            assert db_path.exists()
        finally:
            conn.close()

    def test_sets_wal_mode(self, tmp_path: Path) -> None:
        conn = open_writer_connection(tmp_path / "memory.db")
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_migrate_creates_all_v1_tables_and_indexes(self, tmp_path: Path) -> None:
        conn = open_writer_connection(tmp_path / "memory.db")
        try:
            tables = _list_tables(conn)
            indexes = _list_indexes(conn)
            assert {"messages", "sessions", "facts", "schema_version"} <= tables
            assert {"idx_messages_session", "idx_messages_ts"} <= indexes
        finally:
            conn.close()

    def test_schema_version_recorded(self, tmp_path: Path) -> None:
        conn = open_writer_connection(tmp_path / "memory.db")
        try:
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            assert version == 1
        finally:
            conn.close()

    def test_reopen_is_noop(self, tmp_path: Path) -> None:
        """migrate() is idempotent — reopening the same DB doesn't duplicate schema."""
        db_path = tmp_path / "memory.db"
        conn1 = open_writer_connection(db_path)
        # Insert a row so we can prove the DB contents survive re-migration.
        conn1.execute(
            "INSERT INTO sessions (session_id, started_at) VALUES (?, ?)",
            ("s1", 1000),
        )
        conn1.commit()
        conn1.close()

        conn2 = open_writer_connection(db_path)
        try:
            rows = conn2.execute("SELECT session_id FROM sessions").fetchall()
            assert [r[0] for r in rows] == ["s1"]
            # Schema version still 1, not doubled
            version = conn2.execute("SELECT version FROM schema_version").fetchone()[0]
            assert version == 1
        finally:
            conn2.close()

    def test_messages_schema_accepts_row(self, tmp_path: Path) -> None:
        conn = open_writer_connection(tmp_path / "memory.db")
        try:
            conn.execute(
                "INSERT INTO messages (turn_id, session_id, ts, role, content)"
                " VALUES (?, ?, ?, ?, ?)",
                ("t1", "s1", 1000, "user", "hi"),
            )
            conn.commit()
            row = conn.execute(
                "SELECT turn_id, session_id, role, content FROM messages"
            ).fetchone()
            assert row == ("t1", "s1", "user", "hi")
        finally:
            conn.close()

    def test_facts_content_is_unique(self, tmp_path: Path) -> None:
        conn = open_writer_connection(tmp_path / "memory.db")
        try:
            conn.execute(
                "INSERT INTO facts (content, source_session, first_seen, last_confirmed)"
                " VALUES (?, ?, ?, ?)",
                ("Matt lives in Upland", "s1", 1000, 1000),
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO facts (content, source_session, first_seen, last_confirmed)"
                    " VALUES (?, ?, ?, ?)",
                    ("Matt lives in Upland", "s2", 2000, 2000),
                )
                conn.commit()
        finally:
            conn.close()

    def test_open_against_directory_raises(self, tmp_path: Path) -> None:
        # Tmp_path itself is a dir; trying to open it as a DB file should fail
        # before silently partial-initialising.
        with pytest.raises(Exception):  # noqa: B017 — sqlite3 or OSError both acceptable
            open_writer_connection(tmp_path)


class TestOpenReaderConnection:
    def test_allows_cross_thread_reads(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        writer = open_writer_connection(db_path)
        try:
            writer.execute(
                "INSERT INTO sessions (session_id, started_at) VALUES (?, ?)",
                ("s1", 1000),
            )
            writer.commit()
        finally:
            writer.close()

        reader = open_reader_connection(db_path)
        try:
            # Reading from a different thread than the reader was opened in
            # must NOT raise ProgrammingError — that's the whole point of
            # check_same_thread=False.
            result: list[str] = []
            exc: list[BaseException] = []

            def _read() -> None:
                try:
                    row = reader.execute("SELECT session_id FROM sessions").fetchone()
                    result.append(row[0])
                except BaseException as e:
                    exc.append(e)

            t = threading.Thread(target=_read)
            t.start()
            t.join()
            assert exc == []
            assert result == ["s1"]
        finally:
            reader.close()


class TestConcurrentReaderWriter:
    def test_reader_sees_committed_writes_concurrently(self, tmp_path: Path) -> None:
        """WAL allows concurrent readers during a writer's active work — neither blocks."""
        db_path = tmp_path / "memory.db"
        writer = open_writer_connection(db_path)
        reader = open_reader_connection(db_path)
        try:
            writer.execute(
                "INSERT INTO sessions (session_id, started_at) VALUES (?, ?)",
                ("s1", 1000),
            )
            writer.commit()

            # Immediately from the reader, the new row is visible.
            row = reader.execute(
                "SELECT session_id FROM sessions WHERE session_id=?", ("s1",)
            ).fetchone()
            assert row == ("s1",)
        finally:
            writer.close()
            reader.close()


class TestMigrateIdempotent:
    def test_migrate_called_twice_is_safe(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        conn = open_writer_connection(db_path)  # runs migrate once
        try:
            # Running migrate a second time on the same connection must not
            # raise duplicate-object errors.
            migrate(conn)
            # Schema version still 1, no duplicate rows.
            rows = conn.execute("SELECT version FROM schema_version").fetchall()
            assert rows == [(1,)]
        finally:
            conn.close()
