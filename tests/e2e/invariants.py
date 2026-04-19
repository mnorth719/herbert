"""Invariant assertions shared across every e2e scenario.

Each invariant captures a property that should hold for ANY successful turn,
regardless of the specific inputs. The scenario-specific assertions (state
sequence, session content, error class) live in each test; these invariants
catch whole categories of regressions even when the test author forgets to
check them explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence

from herbert.events import StateChanged
from herbert.session import Message


def assert_state_sequence(
    observed: Sequence[StateChanged],
    expected: Sequence[str],
) -> None:
    """Compare the ordered StateChanged events against an "from->to" list."""
    actual = [f"{e.from_state}->{e.to_state}" for e in observed]
    assert actual == list(expected), (
        f"state sequence mismatch:\n  expected: {list(expected)}\n  actual:   {actual}"
    )


def assert_session_alternates(messages: Sequence[Message]) -> None:
    """The Anthropic API rejects two consecutive same-role messages.

    This invariant fires if any cancellation/error reconciliation path leaves
    the session in an illegal shape. Fires once per boundary so failure
    messages name the exact index.
    """
    for i in range(1, len(messages)):
        assert messages[i].role != messages[i - 1].role, (
            f"session role-alternation broken at index {i}: "
            f"{messages[i - 1].role} → {messages[i].role}. "
            f"Full messages: {[(m.role, m.content) for m in messages]}"
        )


def assert_session_matches(
    messages: Sequence[Message],
    expected: list[tuple[str, str]],
) -> None:
    """Exact session equality with readable failure output."""
    actual = [(m.role, m.content) for m in messages]
    assert actual == expected, (
        f"session content mismatch:\n  expected: {expected}\n  actual:   {actual}"
    )


def assert_no_orphan_assistant(messages: Sequence[Message]) -> None:
    """An assistant message must always be preceded by a user message."""
    for i, msg in enumerate(messages):
        if msg.role == "assistant":
            assert i > 0 and messages[i - 1].role == "user", (
                f"orphan assistant message at index {i} (no preceding user). "
                f"Full messages: {[(m.role, m.content) for m in messages]}"
            )


def assert_interrupted_marker(messages: Sequence[Message]) -> None:
    """If any assistant message was interrupted, the marker must end it."""
    for msg in messages:
        if msg.role == "assistant" and "[interrupted]" in msg.content:
            assert msg.content.rstrip().endswith("[interrupted]"), (
                f"[interrupted] marker must be the suffix, got: {msg.content!r}"
            )
