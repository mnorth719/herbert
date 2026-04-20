"""Daemon + MemoryStore integration: end-to-end memory flow.

Drives a daemon with a real ``MemoryStore`` backing ``SqliteSession`` and
verifies the product-observable behaviours:
  - First turn appends rows to ``messages``.
  - Session close (simulated inactivity) seals ``sessions.ended_at`` and
    runs extraction; the resulting facts + summary show up in the next
    session's system prompt.
  - When memory is disabled (``config.memory.enabled=False``), no DB file
    is created and the daemon reverts to ``InMemorySession``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from herbert.config import HerbertConfig, MemoryConfig
from herbert.daemon import Daemon, DaemonDeps
from herbert.events import AsyncEventBus, TurnCompleted
from herbert.hal import Hal
from herbert.hal.mock import MockAudioIn, MockAudioOut, MockEventSource
from herbert.memory.store import MemoryStore
from herbert.session import InMemorySession, Session, SqliteSession
from herbert.stt import SttResult
from herbert.tts import TtsState

# --- stubs (mirror tests/integration/test_daemon_pipeline.py) ---------------


class _StubStt:
    def __init__(self, text: str) -> None:
        self._text = text

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> SttResult:
        return SttResult(text=self._text, duration_ms=100)


class _TextDelta:
    type = "text_delta"

    def __init__(self, text: str) -> None:
        self.text = text


class _DeltaEvent:
    type = "content_block_delta"

    def __init__(self, text: str) -> None:
        self.delta = _TextDelta(text)


class _StubStream:
    def __init__(self, deltas: list[str]) -> None:
        self._events = [_DeltaEvent(d) for d in deltas]
        self.recorded_system: str = ""

    async def __aenter__(self) -> _StubStream:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for e in self._events:
                await asyncio.sleep(0)
                yield e

        return _gen()


class _StubMessages:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.recorded_system: list[str] = []
        self.create_responses: list[str] = []  # for extractor calls
        self._create_idx = 0

    def stream(self, **kwargs: Any) -> _StubStream:
        self.recorded_system.append(kwargs.get("system", ""))
        return _StubStream(self._deltas)

    async def create(self, **kwargs: Any) -> Any:
        """Used by the extractor — returns a scripted JSON text response."""
        if self._create_idx >= len(self.create_responses):
            raise RuntimeError("stub out of create responses")
        text = self.create_responses[self._create_idx]
        self._create_idx += 1

        class _Block:
            type = "text"

            def __init__(self, t: str) -> None:
                self.text = t

        class _Resp:
            def __init__(self, t: str) -> None:
                self.content = [_Block(t)]

        return _Resp(text)


class _StubClient:
    def __init__(self, deltas: list[str]) -> None:
        self.messages = _StubMessages(deltas)


class _StubTts:
    sample_rate: int = 16000

    async def stream(
        self, sentences: AsyncIterator[str], state: TtsState | None = None
    ) -> AsyncIterator[bytes]:
        async for _ in sentences:
            await asyncio.sleep(0)
            if state is not None and state.ttfb_ms is None:
                state.ttfb_ms = 50
                state.chunks_produced = 1
            yield b"\x00" * 100


class _SlowMockAudioOut(MockAudioOut):
    async def play(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None:
        self.sample_rate = sample_rate
        async for chunk in chunks:
            self.played.append(chunk)


def _build_hal() -> tuple[Hal, MockEventSource, MockAudioIn, _SlowMockAudioOut]:
    src = MockEventSource()
    ain = MockAudioIn(pcm=b"\x01\x00" * 1600)
    aout = _SlowMockAudioOut()
    hal = Hal(platform="mock", event_source=src, audio_in=ain, audio_out=aout)
    return hal, src, ain, aout


async def _drive_turn(
    daemon_task: asyncio.Task, bus: AsyncEventBus, src: MockEventSource
) -> None:
    """Press+release the button and wait for TurnCompleted."""
    done = asyncio.Event()

    async def _wait() -> None:
        async with bus.subscribe() as sub:
            while True:
                event = await sub.receive()
                if isinstance(event, TurnCompleted):
                    done.set()
                    return

    waiter = asyncio.create_task(_wait())
    await src.press()
    await asyncio.sleep(0.05)
    await src.release()
    await asyncio.wait_for(done.wait(), timeout=2.0)
    waiter.cancel()
    try:
        await waiter
    except asyncio.CancelledError:
        pass


def _build_deps(
    *,
    config: HerbertConfig,
    bus: AsyncEventBus,
    stt_text: str,
    llm_deltas: list[str],
    store: MemoryStore | None = None,
    session_factory=None,  # type: ignore[no-untyped-def]
) -> tuple[DaemonDeps, _StubClient, _SlowMockAudioOut, MockEventSource]:
    hal, src, _ain, aout = _build_hal()
    client = _StubClient(llm_deltas)
    deps = DaemonDeps(
        config=config,
        bus=bus,
        hal=hal,
        stt=_StubStt(stt_text),
        tts=_StubTts(),
        llm_client=client,
        persona="TEST PERSONA",
        store=store,
        session_factory=session_factory,
    )
    return deps, client, aout, src


# --- tests ------------------------------------------------------------------


class TestMemoryEnabledPath:
    async def test_first_turn_persists_user_and_assistant(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path)
        try:
            bus = AsyncEventBus()
            config = HerbertConfig(memory=MemoryConfig(db_path=db_path))

            def factory() -> Session:
                return SqliteSession(store, store.start_session())

            deps, _client, _aout, src = _build_deps(
                config=config,
                bus=bus,
                stt_text="Hi, I'm Matt and I live in Upland.",
                llm_deltas=["Hello Matt. ", "Nice to meet you.\n"],
                store=store,
                session_factory=factory,
            )
            daemon = Daemon(deps)
            runner = asyncio.create_task(daemon.run())
            try:
                await _drive_turn(runner, bus, src)
                store.drain(timeout=2.0)

                # Session is live and has both messages
                assert daemon.session is not None
                session_id = daemon.session.session_id  # type: ignore[union-attr]
                turns = store.get_session_turns(session_id)
                assert [t[0] for t in turns] == ["user", "assistant"]
                assert turns[0][1] == "Hi, I'm Matt and I live in Upland."
                assert "Hello Matt" in turns[1][1]
            finally:
                await daemon.stop()
                try:
                    await asyncio.wait_for(runner, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
        finally:
            store.close()

    async def test_session_close_extraction_roundtrips_to_next_session_prompt(
        self, tmp_path: Path
    ) -> None:
        """End-to-end: two sessions with a simulated inactivity close between
        them. Extraction facts + summary land in session 2's system prompt."""
        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path)
        try:
            bus = AsyncEventBus()
            # Short inactivity so the timer fires quickly if we let it run;
            # we call _close_current_session directly for determinism.
            config = HerbertConfig(
                memory=MemoryConfig(db_path=db_path, inactivity_seconds=1)
            )

            def factory() -> Session:
                return SqliteSession(store, store.start_session())

            deps, client, _aout, src = _build_deps(
                config=config,
                bus=bus,
                stt_text="I'm Matt from Upland",
                llm_deltas=["Got it. ", "Welcome.\n"],
                store=store,
                session_factory=factory,
            )
            # Script the extractor response for session 1.
            client.messages.create_responses.append(
                json.dumps(
                    {
                        "summary": "Introductions — Matt introduced himself",
                        "new_facts": ["Matt lives in Upland"],
                    }
                )
            )
            daemon = Daemon(deps)
            runner = asyncio.create_task(daemon.run())
            try:
                # --- session 1 ---
                await _drive_turn(runner, bus, src)
                store.drain(timeout=2.0)

                # Simulate inactivity close directly (determinism beats a real sleep)
                await daemon._close_current_session()
                # Wait for the extraction background task to finish
                for t in list(daemon._extraction_tasks):
                    await t
                store.drain(timeout=2.0)

                # Summary + facts should now be queryable
                assert "Matt lives in Upland" in store.get_facts()
                summaries = store.get_recent_summaries(5)
                assert len(summaries) == 1
                assert "Matt introduced himself" in summaries[0][0]

                # --- session 2: a new turn should see session 1's memory ---
                client.messages.recorded_system.clear()
                # Reprogram STT/LLM so we can drive a second turn
                deps.stt = _StubStt("what's my name?")  # type: ignore[attr-defined]
                # Script a plain response (no tool_use, stop_reason end_turn)
                # The existing _StubClient reuses the same deltas.
                await _drive_turn(runner, bus, src)
                store.drain(timeout=2.0)

                # The system prompt sent to Claude must include both memory sections.
                system_prompts = client.messages.recorded_system
                assert len(system_prompts) >= 1
                final_prompt = system_prompts[-1]
                assert "## What I know about Matt" in final_prompt
                assert "Matt lives in Upland" in final_prompt
                assert "## Recent sessions" in final_prompt
                assert "Matt introduced himself" in final_prompt
            finally:
                await daemon.stop()
                try:
                    await asyncio.wait_for(runner, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
        finally:
            store.close()


class TestMemoryDisabledPath:
    async def test_no_db_file_created(self, tmp_path: Path) -> None:
        """memory.enabled=False → daemon uses InMemorySession; no DB on disk."""
        db_path = tmp_path / "memory.db"
        bus = AsyncEventBus()
        config = HerbertConfig(memory=MemoryConfig(enabled=False, db_path=db_path))
        deps, _client, _aout, src = _build_deps(
            config=config,
            bus=bus,
            stt_text="hi",
            llm_deltas=["hey.\n"],
            store=None,
            session_factory=None,
        )
        # Explicit session injection — this is the path Daemon takes when
        # neither memory nor a factory is wired.
        daemon = Daemon(deps, session=InMemorySession())
        runner = asyncio.create_task(daemon.run())
        try:
            await _drive_turn(runner, bus, src)
            # No DB file was created
            assert not db_path.exists()
            # Session is InMemorySession
            assert isinstance(daemon.session, InMemorySession)
        finally:
            await daemon.stop()
            try:
                await asyncio.wait_for(runner, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass


class TestInactivityTimer:
    async def test_timer_not_scheduled_when_memory_disabled(
        self, tmp_path: Path
    ) -> None:
        """Without a store wired in, the inactivity task never starts."""
        bus = AsyncEventBus()
        config = HerbertConfig(memory=MemoryConfig(enabled=False))
        deps, _client, _aout, src = _build_deps(
            config=config,
            bus=bus,
            stt_text="hi",
            llm_deltas=["hey.\n"],
        )
        daemon = Daemon(deps, session=InMemorySession())
        runner = asyncio.create_task(daemon.run())
        try:
            await _drive_turn(runner, bus, src)
            # No inactivity task ever created
            assert daemon._inactivity_task is None  # type: ignore[attr-defined]
        finally:
            await daemon.stop()
            try:
                await asyncio.wait_for(runner, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass


# Silence the "unused replace" warning — imported above for potential reuse
_ = replace
