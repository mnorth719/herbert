"""Conversation session abstraction.

v1 is in-memory and ephemeral — each daemon run starts from zero. v3 will
swap in a SQLite-backed implementation behind the same `Session` Protocol
(see plan Unit 13), so callers only ever depend on the interface.

The API is intentionally narrow. Roles must alternate (user → assistant →
user → …), which the Anthropic API enforces; we do not validate this in
the session itself because barge-in cleanup is the caller's job (the state
machine may `pop_last()` to roll back a user message if no tokens arrived).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

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
