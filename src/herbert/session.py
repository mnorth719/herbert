"""Conversation session abstraction.

Two implementations share the same narrow `Session` Protocol:

  - `InMemorySession` — a plain list in RAM, reset on restart. Used when
    `config.memory.enabled` is False and in unit tests.
  - `SqliteSession` — mirrors an in-memory list to a `MemoryStore` so the
    conversation persists across restarts. Facts + summaries extracted
    at session close feed back into the next session's system prompt.

The API is intentionally narrow. Roles must alternate (user → assistant →
user → …), which the Anthropic API enforces; we do not validate this in
the session itself because barge-in cleanup is the caller's job (the state
machine may `pop_last()` to roll back a user message if no tokens arrived).

`replace_last` is an instance-only method — not part of the Protocol.
`daemon.py` uses `hasattr(session, "replace_last")` to guard the call so
that adding or removing it doesn't change the Protocol surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from herbert.memory.store import MemoryStore

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@runtime_checkable
class Session(Protocol):
    """The minimal contract Herbert depends on for conversation history."""

    @property
    def messages(self) -> list[Message]: ...

    def append(self, msg: Message) -> None: ...

    def clear(self) -> None: ...

    def pop_last(self) -> Message | None: ...


class InMemorySession:
    """The v1 default: a list in RAM, reset on restart."""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        # Return a snapshot so callers can't mutate history through this ref
        return list(self._messages)

    def append(self, msg: Message) -> None:
        self._messages.append(msg)

    def clear(self) -> None:
        self._messages.clear()

    def pop_last(self) -> Message | None:
        if not self._messages:
            return None
        return self._messages.pop()

    def replace_last(self, msg: Message) -> None:
        """Swap the last message in place — used to append an [interrupted] marker
        to a partial assistant response after barge-in without shifting indices."""
        if not self._messages:
            raise IndexError("replace_last called on empty session")
        self._messages[-1] = msg


class SqliteSession:
    """Session that mirrors every message through to ``MemoryStore``.

    ``MemoryStore.append_turn`` returns immediately with the generated
    ``turn_id``; we capture it alongside each in-memory message so that
    ``pop_last`` / ``replace_last`` can target the exact DB row. The
    store's FIFO queue preserves caller-order, so the INSERT + DELETE
    pair for a zero-token barge-in lands in the right sequence even if
    the INSERT hadn't drained yet at pop time.

    ``.messages`` is served from the in-memory mirror — never a DB read
    on the turn path. Cross-session data is queried through
    ``MemoryStore`` helpers (``get_facts``, ``get_recent_summaries``)
    directly by the daemon, not through this interface.
    """

    def __init__(self, store: MemoryStore, session_id: str) -> None:
        self._store = store
        self._session_id = session_id
        self._messages: list[Message] = []
        self._turn_ids: list[str] = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def append(self, msg: Message) -> None:
        turn_id = self._store.append_turn(self._session_id, msg.role, msg.content)
        self._messages.append(msg)
        self._turn_ids.append(turn_id)

    def clear(self) -> None:
        # In-memory clear only. The DB is a durable record for this session
        # — wiping it would confuse the extractor if a new `clear()` happened
        # right before session close. Tests that need a clean DB should
        # start with a fresh tmp_path + MemoryStore.
        self._messages.clear()
        self._turn_ids.clear()

    def pop_last(self) -> Message | None:
        if not self._messages:
            return None
        msg = self._messages.pop()
        turn_id = self._turn_ids.pop()
        self._store.pop_turn(turn_id)
        return msg

    def replace_last(self, msg: Message) -> None:
        """Swap the last message's role + content in place, both in-memory
        and in the DB (UPDATE on the captured ``turn_id``)."""
        if not self._messages:
            raise IndexError("replace_last called on empty session")
        self._messages[-1] = msg
        turn_id = self._turn_ids[-1]
        self._store.replace_turn(turn_id, msg.role, msg.content)
