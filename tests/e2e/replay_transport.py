"""Deterministic replay providers for daemon e2e scenarios.

Each real provider has a Replay* analogue that conforms to the same Protocol
but pulls its output from a fixture file / inline dict rather than a live
model or API. Scenarios are assembled from:

  - ReplaySttProvider: returns a pre-baked SttResult on transcribe()
  - ReplayLlmClient: yields scripted deltas with preserved inter-delta timing
  - ReplayTtsProvider: emits N chunks per sentence with per-chunk delay

The goal is to exercise the full daemon pipeline (state machine, barge-in,
session reconciliation, error classification) without touching an external
service. Per-delta / per-chunk sleeps are what make this meaningful — they
recreate the streaming semantics that pure mocks would collapse.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from herbert.hal import ButtonEvent, PressEnded, PressStarted
from herbert.stt import SttResult
from herbert.tts import TtsState

# --- STT ---------------------------------------------------------------------


class ReplaySttProvider:
    """Returns a canned transcript for each `transcribe()` call."""

    def __init__(self, text: str, duration_ms: int = 600) -> None:
        self._text = text
        self._duration_ms = duration_ms
        self.calls: list[bytes] = []

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> SttResult:
        self.calls.append(pcm)
        return SttResult(text=self._text, duration_ms=self._duration_ms)


# --- LLM ---------------------------------------------------------------------


@dataclass
class LlmDelta:
    """One scripted step of a replayed Claude stream.

    `t_ms` is the offset from the start of the stream; the replay sleeps
    until that offset before emitting this delta. If `error` is set, the
    replay raises that exception at `t_ms` instead of yielding text.
    """

    t_ms: int
    text: str | None = None
    error: BaseException | None = None


class _ReplayStream:
    def __init__(self, script: list[LlmDelta]) -> None:
        self._script = script

    async def __aenter__(self) -> _ReplayStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        script = self._script

        async def _gen() -> AsyncIterator[str]:
            loop = asyncio.get_running_loop()
            start = loop.time()
            for step in script:
                target = start + step.t_ms / 1000.0
                now = loop.time()
                if now < target:
                    await asyncio.sleep(target - now)
                if step.error is not None:
                    raise step.error
                if step.text:
                    yield step.text

        return _gen()


class _ReplayMessages:
    def __init__(self, script: list[LlmDelta], raise_on_open: BaseException | None) -> None:
        self._script = script
        self._raise_on_open = raise_on_open
        self.last_kwargs: dict[str, Any] = {}

    def stream(self, **kwargs: Any) -> _ReplayStream:
        self.last_kwargs = kwargs
        if self._raise_on_open is not None:
            raise self._raise_on_open
        return _ReplayStream(self._script)


class ReplayLlmClient:
    """Drop-in for `anthropic.AsyncAnthropic` in the daemon's DaemonDeps."""

    def __init__(
        self,
        script: list[LlmDelta] | None = None,
        raise_on_open: BaseException | None = None,
    ) -> None:
        self.messages = _ReplayMessages(script or [], raise_on_open)


# --- TTS ---------------------------------------------------------------------


class ReplayTtsProvider:
    """Synthetic TTS: emits configurable chunks per sentence with a timing model."""

    def __init__(
        self,
        sample_rate: int = 22050,
        chunks_per_sentence: int = 4,
        chunk_bytes: int = 256,
        first_chunk_ms: int = 60,
        per_chunk_ms: int = 15,
    ) -> None:
        self._sample_rate = sample_rate
        self._chunks_per_sentence = chunks_per_sentence
        self._chunk_bytes = chunk_bytes
        self._first_chunk_ms = first_chunk_ms
        self._per_chunk_ms = per_chunk_ms
        self.sentences_received: list[str] = []

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    async def stream(
        self,
        sentences: AsyncIterator[str],
        state: TtsState | None = None,
    ) -> AsyncIterator[bytes]:
        import time

        start = time.perf_counter()
        async for sentence in sentences:
            if not sentence.strip():
                continue
            self.sentences_received.append(sentence)
            # First chunk of the sentence: simulate server round-trip
            await asyncio.sleep(self._first_chunk_ms / 1000.0)
            first_chunk_t = time.perf_counter()
            for i in range(self._chunks_per_sentence):
                if i > 0:
                    await asyncio.sleep(self._per_chunk_ms / 1000.0)
                chunk = bytes([(0x30 + (i & 0x0F))]) * self._chunk_bytes
                if state is not None:
                    state.chunks_produced += 1
                    state.bytes_produced += len(chunk)
                    if state.ttfb_ms is None:
                        state.ttfb_ms = int((first_chunk_t - start) * 1000)
                    if i == 0:
                        state.per_sentence_ttfb_ms.append(
                            int((first_chunk_t - start) * 1000)
                        )
                yield chunk
            if state is not None:
                state.sentences_consumed += 1


# --- Event source + timeline driver -----------------------------------------


@dataclass
class TimelineEvent:
    """Scheduled press/release to inject at a point in the scenario timeline."""

    t_ms: int
    kind: str  # "press_started" or "press_ended"


@dataclass
class ReplayEventSource:
    """Feeds ButtonEvents from a scripted timeline onto the event bus.

    The driver runs its own task; `events()` is the Protocol method the
    daemon iterates. Tests start the driver, let the daemon run to completion,
    then assert on state / session outcomes.
    """

    timeline: list[TimelineEvent] = field(default_factory=list)
    _queue: asyncio.Queue[ButtonEvent | None] = field(init=False)
    _closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue()

    async def drive(self) -> None:
        """Push the scripted events onto the queue at their recorded offsets."""
        import time

        start = time.perf_counter()
        for event in self.timeline:
            target = start + event.t_ms / 1000.0
            now = time.perf_counter()
            if now < target:
                await asyncio.sleep(target - now)
            if event.kind == "press_started":
                await self._queue.put(PressStarted())
            elif event.kind == "press_ended":
                await self._queue.put(PressEnded())
            else:
                raise ValueError(f"unknown timeline kind: {event.kind}")

    async def events(self) -> AsyncIterator[ButtonEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)
