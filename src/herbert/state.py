"""Pipeline state machine: idle → listening → thinking → speaking → (idle | error).

The machine is thin on purpose — it publishes `StateChanged` and owns the
current state string, but the daemon orchestrator drives transitions by
calling `.transition(...)` at well-defined pipeline moments. Invalid
transitions log at DEBUG and no-op rather than raising, which keeps the
daemon robust against duplicate button events from the redundant input
paths (pynput + frontend-forwarded keypresses) without needing explicit
de-dup everywhere.
"""

from __future__ import annotations

import logging

from herbert.events import AsyncEventBus, State, StateChanged

log = logging.getLogger(__name__)

# Any source state can transition to "error" (pipeline failures), and any
# state can be re-entered (no-op). These are enumerated only for logging
# / debugging; the machine does not refuse unknown transitions.
_EXPECTED_TRANSITIONS: set[tuple[State, State]] = {
    ("idle", "listening"),
    ("listening", "thinking"),
    ("listening", "idle"),          # empty utterance — cancel silently
    ("thinking", "speaking"),
    ("thinking", "idle"),           # empty LLM response
    ("speaking", "idle"),
    ("speaking", "listening"),      # barge-in
    ("error", "listening"),         # manual retry via button
    ("error", "idle"),              # auto-recovery
    ("idle", "idle"),               # idempotent transitions are fine
}


class StateMachine:
    """Thin state holder that publishes `StateChanged` on every transition."""

    def __init__(self, bus: AsyncEventBus, initial: State = "idle") -> None:
        self._bus = bus
        self._state: State = initial

    @property
    def state(self) -> State:
        return self._state

    async def transition(self, to_state: State, turn_id: str | None = None) -> bool:
        """Move to `to_state` and publish a `StateChanged` event.

        Returns True if the transition fired, False if no-op'd. Any-state →
        error is always allowed. Same-state transitions are no-ops.
        """
        if to_state == self._state:
            return False
        from_state = self._state
        if to_state != "error" and (from_state, to_state) not in _EXPECTED_TRANSITIONS:
            log.debug("unusual transition %s → %s", from_state, to_state)
        self._state = to_state
        await self._bus.publish(
            StateChanged(turn_id=turn_id, from_state=from_state, to_state=to_state)
        )
        return True

    async def transition_to_error(self, turn_id: str | None = None) -> bool:
        return await self.transition("error", turn_id=turn_id)
