"""macOS HAL adapters.

- `MacEventSource` — listens for spacebar press/release via pynput on its own
  thread; bridges events into asyncio via `loop.call_soon_threadsafe`.
- `SounddeviceAudioIn` / `SounddeviceAudioOut` — sounddevice backed PCM
  streams with name-substring device pinning.

Note on pynput + macOS: global keyboard listening requires the terminal
(Terminal.app / iTerm / VS Code) to have Accessibility permission. Without
it, pynput silently receives no events; the frontend-side spacebar path
(see Unit 8) is the redundant fallback.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from herbert.audio.devices import resolve_input_device, resolve_output_device
from herbert.hal import ButtonEvent, PressEnded, PressStarted

log = logging.getLogger(__name__)


# --- EventSource -------------------------------------------------------------


class MacEventSource:
    """pynput-backed spacebar EventSource.

    The `pynput` listener runs on its own thread. Each press/release edge is
    marshalled onto the asyncio loop via `loop.call_soon_threadsafe`.

    Key-repeat events (OS-level auto-repeat while the user holds the key) are
    suppressed — only the first press and the matching release produce
    `PressStarted` / `PressEnded`.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ButtonEvent | None] = asyncio.Queue()
        self._listener: object | None = None
        self._pressed = False
        self._closed = False

    def _on_press(self, loop: asyncio.AbstractEventLoop, key: object) -> None:
        if not self._is_space(key) or self._pressed:
            return
        self._pressed = True
        loop.call_soon_threadsafe(self._queue.put_nowait, PressStarted())

    def _on_release(self, loop: asyncio.AbstractEventLoop, key: object) -> None:
        if not self._is_space(key) or not self._pressed:
            return
        self._pressed = False
        loop.call_soon_threadsafe(self._queue.put_nowait, PressEnded())

    @staticmethod
    def _is_space(key: object) -> bool:
        try:
            from pynput.keyboard import Key
        except ImportError:
            return False
        return key is Key.space

    async def events(self) -> AsyncIterator[ButtonEvent]:
        loop = asyncio.get_running_loop()
        try:
            from pynput import keyboard
        except ImportError as e:
            raise RuntimeError(
                "pynput is required for Mac EventSource; install herbert with the mac extras"
            ) from e

        self._listener = keyboard.Listener(
            on_press=lambda k: self._on_press(loop, k),
            on_release=lambda k: self._on_release(loop, k),
        )
        self._listener.start()  # type: ignore[attr-defined]
        log.info("mac event source listening for spacebar (requires Accessibility permission)")

        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                yield item
        finally:
            listener = self._listener
            self._listener = None
            if listener is not None:
                listener.stop()  # type: ignore[attr-defined]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)


# --- AudioIn -----------------------------------------------------------------


class SounddeviceAudioIn:
    """Captures 16-bit mono PCM via a `sounddevice.RawInputStream` callback.

    The callback runs on PortAudio's internal thread. Each 20ms block is
    marshalled to the event loop via `loop.call_soon_threadsafe`. The capture
    loop polls the queue at 20Hz rather than racing a three-way `asyncio.wait`
    — polling is simple and 50ms latency on a hold-to-talk release is
    imperceptible.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        device_name: str | None = None,
        blocksize_ms: int = 20,
    ) -> None:
        self.sample_rate = sample_rate
        self._device_name = device_name
        self._blocksize = int(sample_rate * (blocksize_ms / 1000.0))

    async def capture_until_released(
        self, stop: asyncio.Event, max_seconds: float = 30.0
    ) -> bytes:
        import sounddevice as sd  # local import — heavy, platform-conditional

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def callback(indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
            if status:
                log.warning("audio-in callback status: %s", status)
            # `indata` is a numpy ndarray (shape [frames, channels], dtype int16).
            # Copy bytes now — PortAudio reuses its buffer after the callback returns.
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        device = resolve_input_device(self._device_name) if self._device_name else None
        stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self._blocksize,
            callback=callback,
            device=device,
        )

        start_t = loop.time()
        chunks: list[bytes] = []
        with stream:
            while not stop.is_set() and (loop.time() - start_t) < max_seconds:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=0.05)
                    chunks.append(chunk)
                except TimeoutError:
                    continue
            # Drain anything still queued after the stop edge.
            while True:
                try:
                    chunks.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

        return b"".join(chunks)


# --- AudioOut ----------------------------------------------------------------


class SounddeviceAudioOut:
    """Plays PCM chunks to a sounddevice `RawOutputStream`.

    `stream.write()` is synchronous and blocks when PortAudio's buffer is
    full — exactly the backpressure we want. Running it in an executor keeps
    the event loop free for upstream work (LLM streaming, bus fan-out).
    """

    def __init__(self, device_name: str | None = None) -> None:
        self._device_name = device_name

    async def play(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None:
        import sounddevice as sd

        device = resolve_output_device(self._device_name) if self._device_name else None
        stream = sd.RawOutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=device,
        )
        with stream:
            async for chunk in chunks:
                if not chunk:
                    continue
                # `write` blocks on PortAudio's buffer; keep the event loop responsive.
                await asyncio.to_thread(stream.write, chunk)
