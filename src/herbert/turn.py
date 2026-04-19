"""`Turn` — the per-exchange context object passed through the pipeline.

Each Turn carries:
  - turn_id: a ULID that lets log lines, latency events, and the session all
    refer to the same exchange
  - release_event: set when the user's PTT press ends → AudioIn stops capturing
  - cancel_event: set on barge-in → individual stages are expected to notice
    (though asyncio cancellation via task.cancel() is the primary mechanism)
  - span: TurnSpan — per-stage timings, finalized at turn end (populates R6a)
  - llm_state / tts_state: mutable sub-trackers for TTFT / TTFB accounting
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

from herbert.llm.claude import LlmTurnState
from herbert.tts import TtsState


def _mini_ulid() -> str:
    """Cheap time-ordered id without adding a ULID dep (ulid-py is available but
    this is good enough for log correlation within a single boot)."""
    ts = int(datetime.now(UTC).timestamp() * 1000)
    return f"{ts:x}-{secrets.token_hex(4)}"


@dataclass
class TurnSpan:
    """Per-stage wall-clock buckets + miss list (finalised at turn end)."""

    turn_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stage_durations: dict[str, int] = field(default_factory=dict)
    misses: list[str] = field(default_factory=list)
    total_ms: int | None = None

    def record(self, stage: str, duration_ms: int) -> None:
        self.stage_durations[stage] = duration_ms

    def mark_miss(self, stage: str) -> None:
        if stage not in self.misses:
            self.misses.append(stage)


@dataclass
class Turn:
    turn_id: str = field(default_factory=_mini_ulid)
    release_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    span: TurnSpan = field(init=False)
    llm_state: LlmTurnState = field(default_factory=LlmTurnState)
    tts_state: TtsState = field(default_factory=TtsState)
    transcript: str = ""

    def __post_init__(self) -> None:
        self.span = TurnSpan(turn_id=self.turn_id)

    def request_cancel(self) -> None:
        self.cancel_event.set()
        # Also set release_event so an in-progress AudioIn capture exits
        self.release_event.set()
