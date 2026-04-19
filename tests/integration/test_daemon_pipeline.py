"""Daemon orchestration test: drive a full turn through mock HAL + stub providers.

Covers:
- Happy-path state sequence (idle → listening → thinking → speaking → idle)
- Session round-trip (user + assistant messages appended in order)
- Barge-in cancellation (second press during speaking → session reconciled)
- Empty transcript short-circuits the LLM + TTS legs
- Auth error enters error state and stays there until next press
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from herbert.config import HerbertConfig
from herbert.daemon import Daemon, DaemonDeps
from herbert.events import (
    AsyncEventBus,
    ErrorOccurred,
    StateChanged,
    TurnCompleted,
    TurnStarted,
)
from herbert.hal import Hal, Platform
from herbert.hal.mock import MockAudioIn, MockAudioOut, MockEventSource
from herbert.session import InMemorySession
from herbert.stt import SttResult
from herbert.tts import TtsState

# --- Test-only stubs --------------------------------------------------------


class _StubStt:
    def __init__(self, text: str = "hello herbert", duration_ms: int = 120) -> None:
        self._text = text
        self._duration_ms = duration_ms
        self.calls: list[bytes] = []

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> SttResult:
        self.calls.append(pcm)
        return SttResult(text=self._text, duration_ms=self._duration_ms)


class _TextDelta:
    type = "text_delta"

    def __init__(self, text: str) -> None:
        self.text = text


class _DeltaEvent:
    """Mimics Anthropic's RawContentBlockDeltaEvent(text_delta)."""

    type = "content_block_delta"

    def __init__(self, text: str) -> None:
        self.delta = _TextDelta(text)


class _StubStream:
    def __init__(self, deltas: list[str]) -> None:
        self._events = [_DeltaEvent(d) for d in deltas]

    async def __aenter__(self) -> _StubStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for e in self._events:
                await asyncio.sleep(0)
                yield e

        return _gen()


class _StubMessages:
    def __init__(self, deltas: list[str], error: Exception | None = None) -> None:
        self._deltas = deltas
        self._error = error

    def stream(self, **kwargs: Any) -> _StubStream:
        if self._error is not None:
            raise self._error
        return _StubStream(self._deltas)


class _StubClient:
    def __init__(self, deltas: list[str], error: Exception | None = None) -> None:
        self.messages = _StubMessages(deltas, error)


class _StubTts:
    sample_rate: int = 16000

    def __init__(self, chunks_per_sentence: int = 2) -> None:
        self._chunks_per_sentence = chunks_per_sentence
        self.sentences_received: list[str] = []

    async def stream(
        self, sentences: AsyncIterator[str], state: TtsState | None = None
    ) -> AsyncIterator[bytes]:
        async for sentence in sentences:
            self.sentences_received.append(sentence)
            for i in range(self._chunks_per_sentence):
                # Let control yield so the daemon can process state transitions
                await asyncio.sleep(0)
                if state is not None:
                    state.chunks_produced += 1
                    state.bytes_produced += 100
                    if state.ttfb_ms is None:
                        state.ttfb_ms = 50
                yield bytes([0x20 + i]) * 100


# --- Fixtures ---------------------------------------------------------------


class _SlowMockAudioOut(MockAudioOut):
    """MockAudioOut that sleeps per chunk to simulate real playback duration.

    Without this, the 'speaking' phase of a mock turn completes in a single
    scheduler tick and tests have no window to observe it or barge-in on it.
    """

    def __init__(self, per_chunk_delay: float = 0.0) -> None:
        super().__init__()
        self._per_chunk_delay = per_chunk_delay

    async def play(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None:
        self.sample_rate = sample_rate
        async for chunk in chunks:
            self.played.append(chunk)
            if self._per_chunk_delay:
                await asyncio.sleep(self._per_chunk_delay)


def _build_hal(
    platform: Platform = "mock", *, per_chunk_delay: float = 0.0
) -> tuple[Hal, MockEventSource, MockAudioIn, _SlowMockAudioOut]:
    src = MockEventSource()
    ain = MockAudioIn(pcm=b"\x01\x00" * 1600)
    aout = _SlowMockAudioOut(per_chunk_delay=per_chunk_delay)
    hal = Hal(platform=platform, event_source=src, audio_in=ain, audio_out=aout)
    return hal, src, ain, aout


def _build_daemon(
    llm_deltas: list[str],
    *,
    llm_error: Exception | None = None,
    stt_text: str = "hello herbert",
    tts: _StubTts | None = None,
    per_chunk_delay: float = 0.0,
) -> tuple[Daemon, AsyncEventBus, MockEventSource, MockAudioOut, _StubTts]:
    hal, src, _ain, aout = _build_hal(per_chunk_delay=per_chunk_delay)
    bus = AsyncEventBus()
    tts_prov = tts or _StubTts()
    deps = DaemonDeps(
        config=HerbertConfig(),
        bus=bus,
        hal=hal,
        stt=_StubStt(text=stt_text),
        tts=tts_prov,
        llm_client=_StubClient(llm_deltas, error=llm_error),
        persona="TEST PERSONA",
    )
    return Daemon(deps, session=InMemorySession()), bus, src, aout, tts_prov


async def _collect_state_changes(bus: AsyncEventBus, stop: asyncio.Event) -> list[str]:
    collected: list[str] = []
    async with bus.subscribe() as sub:
        while not stop.is_set():
            try:
                event = await asyncio.wait_for(sub.receive(), timeout=0.05)
            except TimeoutError:
                continue
            if isinstance(event, StateChanged):
                collected.append(f"{event.from_state}->{event.to_state}")
    return collected


# --- Tests ------------------------------------------------------------------


class TestHappyPath:
    async def test_full_turn_sequence(self) -> None:
        daemon, bus, src, aout, tts = _build_daemon(["Hello there. ", "How are you?\n"])

        collected: list[str] = []

        async def _collector() -> None:
            async with bus.subscribe() as sub:
                while True:
                    event = await sub.receive()
                    if isinstance(event, StateChanged):
                        collected.append(f"{event.from_state}->{event.to_state}")
                    if isinstance(event, TurnCompleted):
                        return

        collector = asyncio.create_task(_collector())
        runner = asyncio.create_task(daemon.run())

        # Drive the button: press, then release after a tick so capture starts
        await src.press()
        await asyncio.sleep(0.05)
        await src.release()

        await asyncio.wait_for(collector, timeout=2.0)
        assert collected == [
            "idle->listening",
            "listening->thinking",
            "thinking->speaking",
            "speaking->idle",
        ]

        # TTS received the complete sentences
        assert tts.sentences_received == ["Hello there.", "How are you?"]

        # Audio out played some PCM
        assert aout.total_bytes > 0

        # Session round-trip
        roles = [m.role for m in daemon.session.messages]
        assert roles == ["user", "assistant"]
        assert daemon.session.messages[0].content == "hello herbert"
        assert daemon.session.messages[1].content == "Hello there. How are you?\n"

        await daemon.stop()
        await src.close()
        await runner


class TestEmptyTranscript:
    async def test_empty_transcript_skips_llm(self) -> None:
        daemon, bus, src, _aout, tts = _build_daemon(
            ["Should not be called. "], stt_text=""
        )
        collected: list[str] = []

        async def _collector() -> None:
            async with bus.subscribe() as sub:
                while True:
                    event = await sub.receive()
                    if isinstance(event, StateChanged):
                        collected.append(f"{event.from_state}->{event.to_state}")
                    if isinstance(event, TurnCompleted):
                        return

        collector = asyncio.create_task(_collector())
        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        await asyncio.wait_for(collector, timeout=2.0)

        assert collected == [
            "idle->listening",
            "listening->thinking",
            "thinking->idle",
        ]
        assert tts.sentences_received == []
        assert daemon.session.messages == []  # no append

        await daemon.stop()
        await src.close()
        await runner


class TestBargeIn:
    async def test_second_press_during_speaking_cancels_and_reconciles(self) -> None:
        # Many chunks so TTS keeps producing — gives us a large barge-in window
        tts = _StubTts(chunks_per_sentence=50)
        daemon, bus, src, _aout, _ = _build_daemon(
            ["This is a long sentence. ", "Here is another one. "],
            tts=tts,
            per_chunk_delay=0.005,  # ~250ms total speaking window
        )
        events_seen: list[Any] = []

        async def _collector() -> None:
            async with bus.subscribe() as sub:
                while True:
                    event = await sub.receive()
                    events_seen.append(event)

        collector = asyncio.create_task(_collector())
        runner = asyncio.create_task(daemon.run())

        # Turn 1: press → release → pipeline runs
        await src.press()
        await asyncio.sleep(0.02)
        await src.release()

        # Wait until we're in speaking, then barge-in
        await _wait_for_state(daemon, "speaking", timeout=2.0)
        await src.press()  # barge-in
        # Turn 2 starts in listening
        await _wait_for_state(daemon, "listening", timeout=1.0)

        # Cancelled turn should be reflected in TurnCompleted outcome
        cancelled = [
            e for e in events_seen if isinstance(e, TurnCompleted) and e.outcome == "cancelled"
        ]
        assert len(cancelled) == 1

        # Session: assistant message should be marked [interrupted] since some
        # tokens had been received. Alternation is preserved for turn 2.
        roles = [m.role for m in daemon.session.messages]
        assert roles == ["user", "assistant"]
        assert daemon.session.messages[-1].content.endswith("[interrupted]")

        # Complete turn 2 cleanly
        await src.release()
        await asyncio.sleep(0.05)

        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass
        await daemon.stop()
        await src.close()
        await runner

    async def test_barge_in_before_any_token_pops_user_msg(self) -> None:
        # Slow delta so cancel fires before anything arrives
        class _SlowStream(_StubStream):
            def __aiter__(self) -> AsyncIterator[Any]:
                async def _gen() -> AsyncIterator[Any]:
                    await asyncio.sleep(0.5)
                    yield _DeltaEvent("Never arrives. ")

                return _gen()

        class _SlowMessages(_StubMessages):
            def stream(self, **kwargs: Any) -> _SlowStream:
                return _SlowStream([])

        class _SlowClient:
            messages = _SlowMessages([])

        hal, src, _ain, _aout = _build_hal()
        bus = AsyncEventBus()
        deps = DaemonDeps(
            config=HerbertConfig(),
            bus=bus,
            hal=hal,
            stt=_StubStt(text="ask away"),
            tts=_StubTts(),
            llm_client=_SlowClient(),
            persona="p",
        )
        daemon = Daemon(deps, session=InMemorySession())
        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        await _wait_for_state(daemon, "thinking", timeout=1.0)
        # Barge-in before any token arrives
        await src.press()
        await asyncio.sleep(0.1)

        # User message should have been popped (no tokens → no assistant content)
        assert daemon.session.messages == []

        await daemon.stop()
        await src.close()
        await runner


class TestErrorHandling:
    async def test_auth_error_enters_error_state(self) -> None:
        class _AuthErr(Exception):
            pass

        daemon, bus, src, _aout, _ = _build_daemon(
            [], llm_error=_AuthErr("authentication failed for key")
        )

        events_seen: list[Any] = []

        async def _collector() -> None:
            async with bus.subscribe() as sub:
                while True:
                    event = await sub.receive()
                    events_seen.append(event)

        collector = asyncio.create_task(_collector())
        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        await _wait_for_state(daemon, "error", timeout=2.0)

        errors = [e for e in events_seen if isinstance(e, ErrorOccurred)]
        assert len(errors) == 1
        assert errors[0].error_class == "api_auth"

        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass
        await daemon.stop()
        await src.close()
        await runner

    async def test_manual_retry_from_error_on_next_press(self) -> None:
        class _AuthErr(Exception):
            pass

        daemon, _bus, src, _aout, _ = _build_daemon(
            ["recovered. "], llm_error=_AuthErr("authentication failed")
        )
        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        await _wait_for_state(daemon, "error", timeout=1.0)

        # Swap the client to a working one, mimicking a key fix between turns
        daemon._deps.llm_client = _StubClient(["Thanks for waiting. "])

        # Press again → transitions error → listening, runs fresh turn
        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        await _wait_for_state(daemon, "idle", timeout=2.0)
        # Session has exactly one successful pair
        roles = [m.role for m in daemon.session.messages]
        assert roles == ["user", "assistant"]

        await daemon.stop()
        await src.close()
        await runner


class TestStartedAndCompleted:
    async def test_turn_started_and_completed_events(self) -> None:
        daemon, bus, src, _aout, _ = _build_daemon(["Hello. "])

        started: list[TurnStarted] = []
        completed: list[TurnCompleted] = []

        async def _collector() -> None:
            async with bus.subscribe() as sub:
                while True:
                    event = await sub.receive()
                    if isinstance(event, TurnStarted):
                        started.append(event)
                    if isinstance(event, TurnCompleted):
                        completed.append(event)
                        return

        collector = asyncio.create_task(_collector())
        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        await asyncio.wait_for(collector, timeout=2.0)

        assert len(started) == 1
        assert len(completed) == 1
        assert started[0].turn_id == completed[0].turn_id
        assert completed[0].outcome == "success"

        await daemon.stop()
        await src.close()
        await runner


# --- helpers ----------------------------------------------------------------


async def _wait_for_state(daemon: Daemon, target: str, *, timeout: float) -> None:
    async def _poll() -> None:
        while daemon.state != target:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout=timeout)
