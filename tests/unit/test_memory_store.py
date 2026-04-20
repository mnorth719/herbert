"""MemoryStore: writer-thread queue, direct reads, lifecycle."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from herbert.memory.store import MemoryStore


def _wait_for_drain(store: MemoryStore, timeout: float = 2.0) -> None:
    """Block until all queued write ops have executed."""
    store.drain(timeout=timeout)


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "memory.db")
    yield s
    s.close()


class TestSessionLifecycle:
    def test_start_session_returns_id_synchronously(self, store: MemoryStore) -> None:
        sid = store.start_session()
        assert isinstance(sid, str) and len(sid) > 0
        _wait_for_drain(store)
        # Row visible after drain
        turns = store.get_session_turns(sid)
        assert turns == []

    def test_close_session_sets_ended_at_and_summary(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.append_turn(sid, "user", "hi")
        store.append_turn(sid, "assistant", "hey")
        store.close_session(sid, summary="chatted briefly", new_facts=[])
        _wait_for_drain(store)

        summaries = store.get_recent_summaries(5)
        assert len(summaries) == 1
        summary, ended_at = summaries[0]
        assert summary == "chatted briefly"
        assert isinstance(ended_at, int) and ended_at > 0

    def test_close_session_inserts_new_facts_atomically(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.append_turn(sid, "user", "I'm Matt, from Upland")
        store.close_session(
            sid,
            summary="introductions",
            new_facts=["Matt is the user", "Matt lives in Upland"],
        )
        _wait_for_drain(store)

        facts = store.get_facts()
        assert "Matt is the user" in facts
        assert "Matt lives in Upland" in facts


class TestTurnOps:
    def test_append_turn_returns_turn_id_synchronously(self, store: MemoryStore) -> None:
        sid = store.start_session()
        turn_id = store.append_turn(sid, "user", "hi")
        assert isinstance(turn_id, str) and len(turn_id) > 0
        _wait_for_drain(store)
        turns = store.get_session_turns(sid)
        assert turns == [("user", "hi")]

    def test_append_then_pop_preserves_fifo_order(self, store: MemoryStore) -> None:
        """FIFO queue guarantees INSERT drains before the later DELETE,
        even if the caller never waited between them."""
        sid = store.start_session()
        turn_id = store.append_turn(sid, "user", "whoops")
        store.pop_turn(turn_id)  # right after, no sleep
        _wait_for_drain(store)

        turns = store.get_session_turns(sid)
        assert turns == []

    def test_replace_turn_updates_role_and_content(self, store: MemoryStore) -> None:
        sid = store.start_session()
        turn_id = store.append_turn(sid, "assistant", "original response")
        store.replace_turn(turn_id, "assistant", "original response [interrupted]")
        _wait_for_drain(store)

        turns = store.get_session_turns(sid)
        assert turns == [("assistant", "original response [interrupted]")]

    def test_get_session_turns_orders_by_ts(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.append_turn(sid, "user", "first")
        # tiny sleep to ensure distinct ts in the rare case the unix-second
        # granularity resolution collapses them
        time.sleep(0.01)
        store.append_turn(sid, "assistant", "second")
        time.sleep(0.01)
        store.append_turn(sid, "user", "third")
        _wait_for_drain(store)

        turns = store.get_session_turns(sid)
        assert [t[1] for t in turns] == ["first", "second", "third"]


class TestFactsDedup:
    def test_duplicate_fact_does_not_insert_new_row(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.close_session(sid, summary=None, new_facts=["Matt lives in Upland"])
        _wait_for_drain(store)

        sid2 = store.start_session()
        store.close_session(sid2, summary=None, new_facts=["Matt lives in Upland"])
        _wait_for_drain(store)

        facts = store.get_facts()
        assert facts.count("Matt lives in Upland") == 1

    def test_duplicate_fact_bumps_last_confirmed(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.close_session(sid, summary=None, new_facts=["Matt is a Dodgers fan"])
        _wait_for_drain(store)
        # Grab the first last_confirmed via the reader connection (public API
        # doesn't surface it; sample via a direct SELECT for the test).
        first_lc = store._reader_execute(
            "SELECT last_confirmed FROM facts WHERE content=?",
            ("Matt is a Dodgers fan",),
        )[0][0]

        time.sleep(1.05)  # ensure integer-second bump
        sid2 = store.start_session()
        store.close_session(sid2, summary=None, new_facts=["Matt is a Dodgers fan"])
        _wait_for_drain(store)
        second_lc = store._reader_execute(
            "SELECT last_confirmed FROM facts WHERE content=?",
            ("Matt is a Dodgers fan",),
        )[0][0]
        assert second_lc > first_lc


class TestSummaryRetrieval:
    def test_get_recent_summaries_orders_newest_first(self, store: MemoryStore) -> None:
        sid1 = store.start_session()
        store.close_session(sid1, summary="first chat", new_facts=[])
        _wait_for_drain(store)
        time.sleep(1.05)
        sid2 = store.start_session()
        store.close_session(sid2, summary="second chat", new_facts=[])
        _wait_for_drain(store)
        time.sleep(1.05)
        sid3 = store.start_session()
        store.close_session(sid3, summary="third chat", new_facts=[])
        _wait_for_drain(store)

        summaries = store.get_recent_summaries(5)
        assert [s[0] for s in summaries] == ["third chat", "second chat", "first chat"]

    def test_get_recent_summaries_limits_n(self, store: MemoryStore) -> None:
        for i in range(5):
            sid = store.start_session()
            store.close_session(sid, summary=f"chat {i}", new_facts=[])
            _wait_for_drain(store)
            time.sleep(0.01)

        summaries = store.get_recent_summaries(3)
        assert len(summaries) == 3

    def test_null_summaries_are_filtered(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.close_session(sid, summary=None, new_facts=[])
        _wait_for_drain(store)

        summaries = store.get_recent_summaries(5)
        assert summaries == []


class TestEmptyStates:
    def test_get_facts_empty_returns_empty_list(self, store: MemoryStore) -> None:
        assert store.get_facts() == []

    def test_get_session_turns_unknown_session_returns_empty(
        self, store: MemoryStore
    ) -> None:
        assert store.get_session_turns("never-was") == []

    def test_close_session_with_no_turns_is_fine(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.close_session(sid, summary=None, new_facts=[])
        _wait_for_drain(store)
        # No exception, session exists.
        rows = store._reader_execute(
            "SELECT ended_at FROM sessions WHERE session_id=?", (sid,)
        )
        assert rows[0][0] is not None


class TestConcurrentReads:
    def test_concurrent_readers_do_not_raise(self, store: MemoryStore) -> None:
        sid = store.start_session()
        store.close_session(sid, summary="x", new_facts=["fact a", "fact b"])
        _wait_for_drain(store)

        errors: list[BaseException] = []

        def _read() -> None:
            try:
                for _ in range(50):
                    store.get_facts()
                    store.get_recent_summaries(5)
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=_read) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


class TestLifecycle:
    def test_close_drains_pending_writes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "memory.db"
        s = MemoryStore(db_path)
        sid = s.start_session()
        # Enqueue many writes then close immediately — close() must drain.
        for i in range(20):
            s.append_turn(sid, "user", f"msg-{i}")
        s.close()

        # Re-open and verify every row landed.
        s2 = MemoryStore(db_path)
        try:
            turns = s2.get_session_turns(sid)
            assert len(turns) == 20
        finally:
            s2.close()

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path / "memory.db")
        s.close()
        # Second call must not raise.
        s.close()

    def test_writer_exception_does_not_kill_thread(self, tmp_path: Path) -> None:
        """A bad op (e.g., UPDATE on a non-existent turn_id) should log
        and keep the writer loop draining subsequent ops."""
        s = MemoryStore(tmp_path / "memory.db")
        try:
            # replace_turn on an unknown turn_id is a no-op at the SQLite
            # level (UPDATE matches zero rows) — not a raise. But closing
            # an unknown session_id should also not raise downstream ops.
            s.replace_turn("unknown-turn-id", "assistant", "should be no-op")
            # Now queue a legitimate write — must succeed.
            sid = s.start_session()
            s.append_turn(sid, "user", "still working")
            _wait_for_drain(s)
            assert s.get_session_turns(sid) == [("user", "still working")]
        finally:
            s.close()
