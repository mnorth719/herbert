"""Typed event models + AsyncEventBus.

The bus is a central nervous system for Herbert: state machine, logger,
WebSocket, and latency instrumentation all communicate through it.

Design notes:
- Single global bus (not topic-per-type) preserves cross-event ordering.
  Subscribers filter by `event_type` themselves.
- Each subscription gets a bounded queue with drop-oldest overflow, so a
  stuck subscriber (e.g. backgrounded browser tab) cannot cause the daemon
  to leak memory.
- Events carry a monotonic `seq` counter so out-of-order delivery between
  subscribers — or later replay/inspection — is detectable.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

# --- Event models ------------------------------------------------------------

State = Literal["idle", "listening", "thinking", "speaking", "error"]
View = Literal["character", "diagnostic"]
ErrorClass = Literal[
    "wifi_down",
    "api_auth",
    "api_rate_limit",
    "api_policy",
    "network_transient",
    "mic_error",
    "speaker_error",
    "whisper_error",
    "tts_error",
    "missing_secrets",
    "persona_invalid",
    "unknown",
]


class _EventBase(BaseModel):
    """Shared envelope fields for every event.

    `seq` is assigned by the bus on publish; leave None at construction.
    """

    model_config = ConfigDict(frozen=False)

    turn_id: str | None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    seq: int | None = None


class StateChanged(_EventBase):
    event_type: Literal["state_changed"] = "state_changed"
    from_state: State
    to_state: State


class TurnStarted(_EventBase):
    event_type: Literal["turn_started"] = "turn_started"
    mode: str  # e.g. "pi_hybrid", "mac_hybrid", "pi_full_local"


class TurnCompleted(_EventBase):
    event_type: Literal["turn_completed"] = "turn_completed"
    outcome: Literal["success", "cancelled", "error"]


class ExchangeLatency(_EventBase):
    event_type: Literal["exchange_latency"] = "exchange_latency"
    total_ms: int
    stage_durations: dict[str, int]  # {"stt": 1100, "llm_ttft": 500, ...}
    misses: list[str]  # stage names that exceeded their ceiling
    mode: str


class LatencyMiss(_EventBase):
    event_type: Literal["latency_miss"] = "latency_miss"
    stage: str  # "stt" | "llm_ttft" | "first_sentence" | "tts_ttfb" | "total"
    actual_ms: int
    ceiling_ms: int
    mode: str
    providers: dict[str, str]  # {"stt": "whisper_cpp", "tts": "elevenlabs"}


class ErrorOccurred(_EventBase):
    event_type: Literal["error_occurred"] = "error_occurred"
    error_class: ErrorClass
    message: str


class ViewChanged(_EventBase):
    event_type: Literal["view_changed"] = "view_changed"
    view: View


class TranscriptDelta(_EventBase):
    event_type: Literal["transcript_delta"] = "transcript_delta"
    role: Literal["user", "assistant"]
    text: str


class LogLine(_EventBase):
    """Frame of the live log tail streamed to the diagnostic view."""

    event_type: Literal["log_line"] = "log_line"
    level: str  # "INFO", "WARN", "ERROR", ...
    line: str


AnyEvent = Annotated[
    StateChanged | TurnStarted | TurnCompleted | ExchangeLatency | LatencyMiss | ErrorOccurred | ViewChanged | TranscriptDelta | LogLine,
    Field(discriminator="event_type"),
]


# --- Bus ---------------------------------------------------------------------


class Subscription:
    """One subscriber's view of the bus. Read via `await sub.receive()`.

    Created via `async with bus.subscribe() as sub`. Backed by a bounded
    asyncio.Queue with drop-oldest overflow; `dropped_count` records how
    many events were discarded due to a slow consumer.
    """

    def __init__(self, queue: asyncio.Queue[_EventBase]) -> None:
        self._queue = queue
        self.dropped_count = 0

    async def receive(self) -> _EventBase:
        return await self._queue.get()

    def __aiter__(self) -> AsyncIterator[_EventBase]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[_EventBase]:
        while True:
            yield await self.receive()


class AsyncEventBus:
    """In-process typed pub/sub bus.

    `queue_maxsize` sets the bound for each subscriber's inbox. Publishing
    never blocks the producer: when a subscriber's queue is full the oldest
    event is dropped and the drop is recorded on that subscription.
    """

    def __init__(self, queue_maxsize: int = 256) -> None:
        self._subscribers: list[Subscription] = []
        self._queue_maxsize = queue_maxsize
        self._seq_counter = itertools.count(1)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: _EventBase) -> None:
        """Fan out the event to every subscriber. Never raises for subscriber errors."""
        event.seq = next(self._seq_counter)
        for sub in list(self._subscribers):
            try:
                sub._queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop-oldest: pull the stalest event off, push the new one
                try:
                    sub._queue.get_nowait()
                    sub.dropped_count += 1
                except asyncio.QueueEmpty:
                    pass
                try:
                    sub._queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Should not happen after the get; log and move on
                    log.warning("event bus: could not enqueue %s after drop", event.event_type)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        queue: asyncio.Queue[_EventBase] = asyncio.Queue(maxsize=self._queue_maxsize)
        sub = Subscription(queue)
        self._subscribers.append(sub)
        try:
            yield sub
        finally:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass
