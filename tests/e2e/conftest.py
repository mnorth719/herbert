"""Daemon factory + event collector used by every e2e scenario."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from herbert.config import HerbertConfig
from herbert.daemon import Daemon, DaemonDeps
from herbert.events import (
    AsyncEventBus,
    ErrorOccurred,
    StateChanged,
    TurnCompleted,
    TurnStarted,
)
from herbert.hal import Hal
from herbert.hal.mock import MockAudioIn
from herbert.session import InMemorySession
from tests.e2e.replay_transport import (
    LlmDelta,
    ReplayEventSource,
    ReplayLlmClient,
    ReplaySttProvider,
    ReplayTtsProvider,
    TimelineEvent,
)


@dataclass
class ScenarioResult:
    """Everything an e2e test needs to assert on after a scenario runs."""

    state_changes: list[StateChanged] = field(default_factory=list)
    turn_starts: list[TurnStarted] = field(default_factory=list)
    turn_completes: list[TurnCompleted] = field(default_factory=list)
    errors: list[ErrorOccurred] = field(default_factory=list)
    all_events: list[Any] = field(default_factory=list)


class _SlowPlayback:
    """Simulates PortAudio's back-pressure: ~15ms per chunk so 'speaking'
    lasts long enough for barge-in and intra-turn assertions to observe it.
    """

    def __init__(self, per_chunk_ms: float = 15.0) -> None:
        self._delay = per_chunk_ms / 1000.0
        self.played: list[bytes] = []
        self.sample_rate: int | None = None

    async def play(self, chunks, sample_rate: int) -> None:  # type: ignore[no-untyped-def]
        self.sample_rate = sample_rate
        async for chunk in chunks:
            self.played.append(chunk)
            if self._delay:
                await asyncio.sleep(self._delay)

    @property
    def total_bytes(self) -> int:
        return sum(len(c) for c in self.played)


@pytest.fixture
def run_scenario():  # type: ignore[no-untyped-def]
    """Returns a function that assembles + drives one e2e scenario."""

    async def _run(
        *,
        stt_text: str,
        stt_duration_ms: int = 600,
        llm_script: list[LlmDelta] | None = None,
        llm_raise_on_open: BaseException | None = None,
        tts_chunks_per_sentence: int = 4,
        tts_chunk_bytes: int = 256,
        tts_first_chunk_ms: int = 60,
        tts_per_chunk_ms: int = 15,
        timeline: list[TimelineEvent] | None = None,
        timeout_s: float = 5.0,
        prior_session_messages: list[tuple[str, str]] | None = None,
    ) -> tuple[Daemon, ScenarioResult, _SlowPlayback]:
        bus = AsyncEventBus()
        src = ReplayEventSource(timeline=timeline or [])
        hal = Hal(
            platform="mock",
            event_source=src,
            audio_in=MockAudioIn(pcm=b"\x01\x00" * 800),
            audio_out=_SlowPlayback(),
        )
        tts = ReplayTtsProvider(
            chunks_per_sentence=tts_chunks_per_sentence,
            chunk_bytes=tts_chunk_bytes,
            first_chunk_ms=tts_first_chunk_ms,
            per_chunk_ms=tts_per_chunk_ms,
        )
        deps = DaemonDeps(
            config=HerbertConfig(),
            bus=bus,
            hal=hal,
            stt=ReplaySttProvider(text=stt_text, duration_ms=stt_duration_ms),
            tts=tts,
            llm_client=ReplayLlmClient(script=llm_script, raise_on_open=llm_raise_on_open),
            persona="e2e persona",
        )
        session = InMemorySession()
        if prior_session_messages:
            from herbert.session import Message

            for role, content in prior_session_messages:
                session.append(Message(role=role, content=content))  # type: ignore[arg-type]

        daemon = Daemon(deps, session=session)
        result = ScenarioResult()

        async def _collector() -> None:
            async with bus.subscribe() as sub:
                while True:
                    event = await sub.receive()
                    result.all_events.append(event)
                    if isinstance(event, StateChanged):
                        result.state_changes.append(event)
                    elif isinstance(event, TurnStarted):
                        result.turn_starts.append(event)
                    elif isinstance(event, TurnCompleted):
                        result.turn_completes.append(event)
                    elif isinstance(event, ErrorOccurred):
                        result.errors.append(event)

        collector_task = asyncio.create_task(_collector())
        runner_task = asyncio.create_task(daemon.run())
        driver_task = asyncio.create_task(src.drive())

        try:
            # Wait for all scripted timeline events to fire, then give the
            # daemon a moment to drain. Scenarios that need explicit end
            # synchronisation should pass enough timeline padding themselves.
            await asyncio.wait_for(driver_task, timeout=timeout_s)
            # Allow pipeline to settle after the final event
            deadline = asyncio.get_running_loop().time() + timeout_s
            while asyncio.get_running_loop().time() < deadline:
                if (
                    daemon._current_task is None or daemon._current_task.done()
                ) and daemon.state in {"idle", "error"}:
                    break
                await asyncio.sleep(0.02)
        finally:
            await daemon.stop()
            await src.close()
            collector_task.cancel()
            try:
                await collector_task
            except asyncio.CancelledError:
                pass
            try:
                await runner_task
            except asyncio.CancelledError:
                pass

        return daemon, result, hal.audio_out  # type: ignore[return-value]

    return _run
