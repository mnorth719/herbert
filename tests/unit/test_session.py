"""Session tests: InMemorySession + SqliteSession share behavioural contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from herbert.memory.store import MemoryStore
from herbert.session import InMemorySession, Message, Session, SqliteSession


class TestInMemorySession:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(InMemorySession(), Session)

    def test_append_and_read(self) -> None:
        s = InMemorySession()
        s.append(Message(role="user", content="hi"))
        s.append(Message(role="assistant", content="hey"))
        assert [m.role for m in s.messages] == ["user", "assistant"]
        assert [m.content for m in s.messages] == ["hi", "hey"]

    def test_messages_returns_snapshot(self) -> None:
        s = InMemorySession()
        s.append(Message(role="user", content="hi"))
        snap = s.messages
        snap.append(Message(role="user", content="mutation"))
        # External mutation of the returned list does not affect the session
        assert len(s.messages) == 1

    def test_pop_last_rolls_back_user_message(self) -> None:
        """Barge-in scenario: we appended a user msg, then got zero tokens back."""
        s = InMemorySession()
        s.append(Message(role="user", content="Hello"))
        popped = s.pop_last()
        assert popped is not None and popped.role == "user"
        assert s.messages == []

    def test_pop_last_empty_returns_none(self) -> None:
        assert InMemorySession().pop_last() is None

    def test_replace_last_swaps_in_place(self) -> None:
        """Barge-in with partial tokens: overwrite the assistant msg with a marked version."""
        s = InMemorySession()
        s.append(Message(role="user", content="Hi"))
        s.append(Message(role="assistant", content="Hello there"))
        s.replace_last(Message(role="assistant", content="Hello there [interrupted]"))
        assert s.messages[-1].content == "Hello there [interrupted]"
        assert len(s.messages) == 2

    def test_replace_last_raises_on_empty(self) -> None:
        import pytest

        with pytest.raises(IndexError):
            InMemorySession().replace_last(Message(role="user", content="x"))

    def test_clear_empties_history(self) -> None:
        s = InMemorySession()
        s.append(Message(role="user", content="hi"))
        s.clear()
        assert s.messages == []


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    s = MemoryStore(tmp_path / "memory.db")
    yield s
    s.close()


def _new_sqlite_session(store: MemoryStore) -> SqliteSession:
    return SqliteSession(store, store.start_session())


class TestSqliteSession:
    def test_satisfies_protocol(self, store: MemoryStore) -> None:
        assert isinstance(_new_sqlite_session(store), Session)

    def test_append_mirrors_to_db(self, store: MemoryStore) -> None:
        s = _new_sqlite_session(store)
        s.append(Message(role="user", content="hi"))
        s.append(Message(role="assistant", content="hey"))
        store.drain(timeout=2.0)
        # In-memory view
        assert [m.content for m in s.messages] == ["hi", "hey"]
        # DB view
        assert store.get_session_turns(s.session_id) == [
            ("user", "hi"),
            ("assistant", "hey"),
        ]

    def test_append_then_pop_fifo_preserves_db(self, store: MemoryStore) -> None:
        """Append + immediate pop — FIFO queue guarantees the DELETE lands
        after the INSERT, so the DB ends up with 0 rows for that turn."""
        s = _new_sqlite_session(store)
        s.append(Message(role="user", content="whoops"))
        popped = s.pop_last()
        assert popped is not None and popped.content == "whoops"
        assert s.messages == []
        store.drain(timeout=2.0)
        assert store.get_session_turns(s.session_id) == []

    def test_pop_last_on_empty_returns_none(self, store: MemoryStore) -> None:
        s = _new_sqlite_session(store)
        assert s.pop_last() is None

    def test_replace_last_updates_both_halves(self, store: MemoryStore) -> None:
        s = _new_sqlite_session(store)
        s.append(Message(role="user", content="Hi"))
        s.append(Message(role="assistant", content="Hello there"))
        s.replace_last(Message(role="assistant", content="Hello there [interrupted]"))
        store.drain(timeout=2.0)
        assert s.messages[-1].content == "Hello there [interrupted]"
        assert store.get_session_turns(s.session_id)[-1] == (
            "assistant",
            "Hello there [interrupted]",
        )

    def test_replace_last_raises_on_empty(self, store: MemoryStore) -> None:
        s = _new_sqlite_session(store)
        with pytest.raises(IndexError):
            s.replace_last(Message(role="user", content="x"))

    def test_clear_empties_in_memory_but_preserves_db(self, store: MemoryStore) -> None:
        s = _new_sqlite_session(store)
        s.append(Message(role="user", content="hi"))
        store.drain(timeout=2.0)
        s.clear()
        assert s.messages == []
        # DB still has the turn — clear() doesn't wipe persistent state
        assert store.get_session_turns(s.session_id) == [("user", "hi")]

    def test_two_sessions_do_not_interfere(self, store: MemoryStore) -> None:
        s1 = _new_sqlite_session(store)
        s2 = _new_sqlite_session(store)
        s1.append(Message(role="user", content="session one"))
        s2.append(Message(role="user", content="session two"))
        store.drain(timeout=2.0)
        assert [m.content for m in s1.messages] == ["session one"]
        assert [m.content for m in s2.messages] == ["session two"]
        assert store.get_session_turns(s1.session_id) == [("user", "session one")]
        assert store.get_session_turns(s2.session_id) == [("user", "session two")]

    def test_barge_in_zero_tokens_cleans_both_halves(self, store: MemoryStore) -> None:
        """Mimics daemon._reconcile_session_after_cancel on zero-token cancel."""
        s = _new_sqlite_session(store)
        # A prior completed turn sits in history.
        s.append(Message(role="user", content="prior turn"))
        s.append(Message(role="assistant", content="prior reply"))
        store.drain(timeout=2.0)
        # New user message appended, then we cancel with zero tokens received.
        s.append(Message(role="user", content="interrupted question"))
        s.pop_last()
        store.drain(timeout=2.0)
        # History is back to the pre-cancel state in both halves.
        assert [m.content for m in s.messages] == ["prior turn", "prior reply"]
        assert store.get_session_turns(s.session_id) == [
            ("user", "prior turn"),
            ("assistant", "prior reply"),
        ]

    def test_barge_in_partial_tokens_marks_both_halves(
        self, store: MemoryStore
    ) -> None:
        """Mimics daemon._reconcile_session_after_cancel on partial-token cancel."""
        s = _new_sqlite_session(store)
        s.append(Message(role="user", content="what's"))
        s.append(Message(role="assistant", content="Well, the answer is"))
        # Daemon overwrites the assistant row with an interrupted marker
        s.replace_last(Message(role="assistant", content="Well, the answer is [interrupted]"))
        store.drain(timeout=2.0)
        assert s.messages[-1].content == "Well, the answer is [interrupted]"
        assert store.get_session_turns(s.session_id) == [
            ("user", "what's"),
            ("assistant", "Well, the answer is [interrupted]"),
        ]
