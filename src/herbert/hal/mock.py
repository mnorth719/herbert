"""In-process test doubles for HAL adapters. Used by unit and e2e tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from herbert.hal import ButtonEvent, PressEnded, PressStarted


class MockEventSource:
    """`EventSource` fed by explicit `press()` / `release()` calls in tests.

    `events()` yields whatever has been pushed; after `close()`, the iterator
    terminates cleanly.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ButtonEvent | None] = asyncio.Queue()
        self._closed = False

    async def press(self) -> None:
        await self._queue.put(PressStarted())

    async def release(self) -> None:
        await self._queue.put(PressEnded())

    def press_nowait(self) -> None:
        self._queue.put_nowait(PressStarted())

    def release_nowait(self) -> None:
        self._queue.put_nowait(PressEnded())

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


class MockAudioIn:
    """`AudioIn` that returns a pre-seeded PCM buffer when `stop` fires.

    Any bytes enqueued via `feed()` before or during the capture are returned
    as a single concatenated buffer. If `stop` never fires, the capture times
    out at `max_seconds` and returns whatever was buffered.
    """

    sample_rate: int

    def __init__(self, sample_rate: int = 16000, pcm: bytes = b"") -> None:
        self.sample_rate = sample_rate
        self._buffer = bytearray(pcm)

    def feed(self, pcm: bytes) -> None:
        self._buffer.extend(pcm)

    async def capture_until_released(
        self, stop: asyncio.Event, max_seconds: float = 30.0
    ) -> bytes:
        try:
            await asyncio.wait_for(stop.wait(), timeout=max_seconds)
        except TimeoutError:
            pass
        return bytes(self._buffer)


class MockAudioOut:
    """`AudioOut` that records every chunk played for test assertions."""

    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.sample_rate: int | None = None

    async def play(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None:
        self.sample_rate = sample_rate
        async for chunk in chunks:
            self.played.append(chunk)

    @property
    def total_bytes(self) -> int:
        return sum(len(c) for c in self.played)
