"""PersonaCache: startup, hot-reload, last-good-cached fallback."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from herbert.persona import PersonaCache, PersonaMissingError

DEFAULT = "DEFAULT_PERSONA_TEXT"


class TestStartupPriming:
    def test_missing_file_uses_default_no_error(self, tmp_path: Path) -> None:
        cache = PersonaCache(tmp_path / "nope.md", default=DEFAULT)
        cache.prime_at_startup()
        assert cache.get_current() == DEFAULT

    def test_present_file_loaded(self, tmp_path: Path) -> None:
        path = tmp_path / "persona.md"
        path.write_text("hello from persona")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()
        assert cache.get_current() == "hello from persona"

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "persona.md"
        path.write_text("   \n\t")
        cache = PersonaCache(path, default=DEFAULT)
        with pytest.raises(PersonaMissingError, match="empty"):
            cache.prime_at_startup()

    def test_unreadable_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "persona.md"
        path.write_text("readable content")

        def _boom(self_inner: Path, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "read_text", _boom)
        cache = PersonaCache(path, default=DEFAULT)
        with pytest.raises(PersonaMissingError, match="unreadable"):
            cache.prime_at_startup()


class TestHotReload:
    def test_edit_file_triggers_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "persona.md"
        path.write_text("version one")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()
        assert cache.get_current() == "version one"

        # Write new content and bump mtime to ensure the reload fires
        time.sleep(0.01)
        path.write_text("version two")
        os.utime(path, (time.time() + 1, time.time() + 1))

        assert cache.get_current() == "version two"

    def test_unchanged_mtime_no_reread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "persona.md"
        path.write_text("stable content")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()

        # Track read_text calls — get_current should not re-read when mtime
        # is unchanged.
        reads = 0
        original = Path.read_text

        def _counting(self_inner: Path, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
            nonlocal reads
            reads += 1
            return original(self_inner, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _counting)
        # Many calls — none should re-read
        for _ in range(5):
            cache.get_current()
        assert reads == 0


class TestLastGoodCached:
    def test_file_deleted_mid_run_keeps_cache(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "persona.md"
        path.write_text("initial")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()
        assert cache.get_current() == "initial"

        path.unlink()
        with caplog.at_level(logging.WARNING, logger="herbert.persona"):
            assert cache.get_current() == "initial"  # cache, not default
        assert any("disappeared" in rec.message for rec in caplog.records)

    def test_file_becomes_empty_keeps_cache(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "persona.md"
        path.write_text("initial")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()
        assert cache.get_current() == "initial"

        time.sleep(0.01)
        path.write_text("")
        os.utime(path, (time.time() + 1, time.time() + 1))
        with caplog.at_level(logging.WARNING, logger="herbert.persona"):
            assert cache.get_current() == "initial"
        assert any("went empty" in rec.message for rec in caplog.records)

    def test_reread_failure_keeps_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = tmp_path / "persona.md"
        path.write_text("initial")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()

        # Now edit to change mtime, but monkey-patch read_text to fail
        time.sleep(0.01)
        path.write_text("next version")
        os.utime(path, (time.time() + 1, time.time() + 1))

        original = Path.read_text
        call_count = 0

        def _intermittent(self_inner: Path, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            raise OSError("transient I/O error")

        monkeypatch.setattr(Path, "read_text", _intermittent)
        with caplog.at_level(logging.WARNING, logger="herbert.persona"):
            assert cache.get_current() == "initial"
        assert any("reread failed" in rec.message for rec in caplog.records)

        # Restore normal read_text; the next call should succeed
        monkeypatch.setattr(Path, "read_text", original)
        assert cache.get_current() == "next version"


class TestConcurrency:
    def test_many_threads_hit_cache_safely(self, tmp_path: Path) -> None:
        import threading

        path = tmp_path / "persona.md"
        path.write_text("shared content")
        cache = PersonaCache(path, default=DEFAULT)
        cache.prime_at_startup()

        results: list[str] = []
        lock = threading.Lock()

        def reader() -> None:
            for _ in range(100):
                val = cache.get_current()
                with lock:
                    results.append(val)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r == "shared content" for r in results)
        assert len(results) == 800
