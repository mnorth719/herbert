"""InMemorySession behavior tests."""

from __future__ import annotations

from herbert.session import InMemorySession, Message, Session


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
