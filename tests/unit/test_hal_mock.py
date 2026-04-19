"""Mock-HAL behavior tests. These double as the executable spec for the protocols."""

from __future__ import annotations

import asyncio

import pytest

from herbert.hal import (
    AudioIn,
    AudioOut,
    EventSource,
    PressEnded,
    PressStarted,
    build_hal,
    detect_platform,
)
from herbert.hal.mock import MockAudioIn, MockAudioOut, MockEventSource


class TestMockEventSource:
    async def test_press_and_release_in_order(self) -> None:
        src = MockEventSource()
        received: list[object] = []

        async def consume() -> None:
            async for evt in src.events():
                received.append(evt)

        task = asyncio.create_task(consume())
        await src.press()
        await src.release()
        await asyncio.sleep(0.01)
        await src.close()
        await task

        assert len(received) == 2
        assert isinstance(received[0], PressStarted)
        assert isinstance(received[1], PressEnded)

    async def test_close_terminates_iterator_cleanly(self) -> None:
        src = MockEventSource()
        await src.close()

        async def consume() -> list[object]:
            return [evt async for evt in src.events()]

        assert await consume() == []

    async def test_satisfies_event_source_protocol(self) -> None:
        src = MockEventSource()
        assert isinstance(src, EventSource)


class TestMockAudioIn:
    async def test_returns_pcm_when_stop_fires(self) -> None:
        pcm = b"\x01\x00" * 1600  # 100ms of single-sample int16 mono
        mic = MockAudioIn(pcm=pcm)
        stop = asyncio.Event()
        stop.set()
        assert await mic.capture_until_released(stop) == pcm

    async def test_captures_fed_bytes(self) -> None:
        mic = MockAudioIn()
        stop = asyncio.Event()

        async def feeder() -> None:
            mic.feed(b"ab")
            mic.feed(b"cd")
            stop.set()

        await asyncio.gather(mic.capture_until_released(stop), feeder())
        # After gather, the capture returned whatever feeder had fed
        assert bytes(mic._buffer) == b"abcd"

    async def test_times_out_without_release(self) -> None:
        mic = MockAudioIn(pcm=b"Z" * 64)
        stop = asyncio.Event()
        result = await mic.capture_until_released(stop, max_seconds=0.05)
        assert result == b"Z" * 64

    async def test_satisfies_audio_in_protocol(self) -> None:
        assert isinstance(MockAudioIn(), AudioIn)


class TestMockAudioOut:
    async def test_records_all_chunks(self) -> None:
        out = MockAudioOut()

        async def produce():
            yield b"aaa"
            yield b"bbb"
            yield b"ccc"

        await out.play(produce(), sample_rate=16000)
        assert out.played == [b"aaa", b"bbb", b"ccc"]
        assert out.total_bytes == 9
        assert out.sample_rate == 16000

    async def test_empty_iterator_does_not_error(self) -> None:
        out = MockAudioOut()

        async def produce():
            return
            yield  # pragma: no cover  (unreachable — makes this a generator)

        await out.play(produce(), sample_rate=22050)
        assert out.played == []

    async def test_satisfies_audio_out_protocol(self) -> None:
        assert isinstance(MockAudioOut(), AudioOut)


class TestPlatformDetect:
    def test_env_override_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERBERT_HAL", "mock")
        assert detect_platform() == "mock"

    def test_env_override_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERBERT_HAL", "windows")
        # Invalid values are ignored in favor of auto-detect
        assert detect_platform() in {"mac", "pi", "mock"}


class TestBuildMockHal:
    def test_build_mock_returns_working_bundle(self) -> None:
        hal = build_hal("mock")
        assert hal.platform == "mock"
        assert isinstance(hal.event_source, EventSource)
        assert isinstance(hal.audio_in, AudioIn)
        assert isinstance(hal.audio_out, AudioOut)

    def test_build_pi_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            build_hal("pi")

    def test_unknown_platform_raises(self) -> None:
        with pytest.raises(ValueError):
            build_hal("windows")  # type: ignore[arg-type]
