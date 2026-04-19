"""Hardware Abstraction Layer — platform-neutral Protocols + factory.

Three Protocols model everything the pipeline needs from the host:

- `EventSource` — push-to-talk button events (GPIO on Pi, spacebar on Mac,
  frontend-forwarded keypresses over the WebSocket on either platform).
- `AudioIn`    — bounded PCM capture driven by an external stop signal.
- `AudioOut`   — streaming PCM playback from an async iterator of chunks.

Application code uses `detect_platform()` + `build_hal(config)` to obtain a
concrete `Hal` bundle. Tests inject `MockHal` directly. There is no
`if platform == "pi"` branching outside this package.
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Platform = Literal["mac", "pi", "mock"]

# --- Button events -----------------------------------------------------------


@dataclass(frozen=True)
class PressStarted:
    """User began holding the push-to-talk button."""


@dataclass(frozen=True)
class PressEnded:
    """User released the push-to-talk button."""


ButtonEvent = PressStarted | PressEnded


# --- Protocols ---------------------------------------------------------------


@runtime_checkable
class EventSource(Protocol):
    """Yields `PressStarted` / `PressEnded` events for the push-to-talk button.

    Must be idempotent across starts: a consumer that stops iterating and
    resumes (e.g. across reconnects) must not observe phantom events.
    """

    async def events(self) -> AsyncIterator[ButtonEvent]: ...

    async def close(self) -> None: ...


@runtime_checkable
class AudioIn(Protocol):
    """PCM capture. Returns 16-bit little-endian mono PCM at `sample_rate`.

    `capture_until_released` drains audio until either `stop` is set (the
    button was released) or `max_seconds` elapses. Returns every PCM byte
    observed between those two moments, in order. No side effects on the
    caller's stop event.
    """

    sample_rate: int

    async def capture_until_released(
        self, stop: asyncio.Event, max_seconds: float = 30.0
    ) -> bytes: ...


@runtime_checkable
class AudioOut(Protocol):
    """Streaming PCM playback. Accepts 16-bit little-endian mono PCM chunks."""

    async def play(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None: ...


# --- Bundle + factory --------------------------------------------------------


@dataclass
class Hal:
    """Container for the three HAL adapters selected for this platform."""

    platform: Platform
    event_source: EventSource
    audio_in: AudioIn
    audio_out: AudioOut


def detect_platform() -> Platform:
    """Pick a HAL platform.

    Precedence:
    1. `HERBERT_HAL=mock|mac|pi` env var (tests + forced overrides)
    2. Auto-detect from `sys.platform` / `platform.machine()`
    """
    forced = os.environ.get("HERBERT_HAL", "").strip().lower()
    if forced in ("mac", "pi", "mock"):
        return forced  # type: ignore[return-value]
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux") and platform.machine() == "aarch64":
        return "pi"
    # Default for unknown Linux / Windows dev envs: mock (no hardware assumptions)
    return "mock"


def build_hal(
    platform_: Platform,
    input_device_name: str | None = None,
    output_device_name: str | None = None,
    input_sample_rate: int = 16000,
) -> Hal:
    """Construct the platform-appropriate `Hal` bundle.

    `input_device_name` / `output_device_name` are substring matches handed to
    the device resolver at stream-open time.
    """
    if platform_ == "mac":
        from herbert.hal.mac import MacEventSource, SounddeviceAudioIn, SounddeviceAudioOut

        return Hal(
            platform="mac",
            event_source=MacEventSource(),
            audio_in=SounddeviceAudioIn(
                sample_rate=input_sample_rate, device_name=input_device_name
            ),
            audio_out=SounddeviceAudioOut(device_name=output_device_name),
        )
    if platform_ == "pi":
        # Pi adapters land in Unit 10 (M4). Until then, force a clear error so
        # cross-platform dev does not silently fall back to something else.
        raise NotImplementedError("Pi HAL adapters land in Unit 10")
    if platform_ == "mock":
        from herbert.hal.mock import MockAudioIn, MockAudioOut, MockEventSource

        return Hal(
            platform="mock",
            event_source=MockEventSource(),
            audio_in=MockAudioIn(),
            audio_out=MockAudioOut(),
        )
    raise ValueError(f"unknown platform: {platform_}")


__all__ = [
    "AudioIn",
    "AudioOut",
    "ButtonEvent",
    "EventSource",
    "Hal",
    "Platform",
    "PressEnded",
    "PressStarted",
    "build_hal",
    "detect_platform",
]
