"""E2E: persistent memory survives a session boundary.

Drives two sessions with a simulated inactivity close between them using
the real replay transport (streamed deltas, timed TTS chunks). The
extractor runs against a stub that returns a canned JSON envelope. Final
assertions:

  - Session 2's first Claude call receives a system prompt that contains
    the facts + summary extracted from session 1.
  - Session 2's `messages` array does NOT include session 1's turns.
  - The SQLite file ends the run in WAL journal mode (proves nothing
    silently reset the pragma mid-test).
  - When extraction raises, session 2 still starts normally with no
    summary contribution but existing facts intact.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from herbert.config import HerbertConfig, MemoryConfig
from herbert.daemon import Daemon, DaemonDeps
from herbert.events import AsyncEventBus, TurnCompleted
from herbert.hal import Hal
from herbert.hal.mock import MockAudioIn
from herbert.memory.store import MemoryStore
from herbert.session import Session, SqliteSession
from tests.e2e.replay_transport import (
    LlmDelta,
    ReplaySttProvider,
    ReplayTtsProvider,
    _ReplayStream,
)


class _HybridMessages:
    """Claude-client stub that speaks both `stream` (for turn calls) and
    `create` (for the extractor). ``stream_scripts`` is consumed in order
    — one per turn call. ``create_responses`` is consumed in order — one
    per extractor call. ``create_raises`` overrides with exceptions when
    set at the matching index."""

    def __init__(
        self,
        *,
        stream_scripts: list[list[LlmDelta]],
        create_responses: list[str] | None = None,
        create_raises: list[Exception | None] | None = None,
    ) -> None:
        self._stream_scripts = list(stream_scripts)
        self._create_responses = list(create_responses or [])
        self._create_raises = list(create_raises or [])
        self._stream_idx = 0
        self._create_idx = 0
        self.recorded_system: list[str] = []

    def stream(self, **kwargs: Any) -> _ReplayStream:
        self.recorded_system.append(kwargs.get("system", ""))
        if self._stream_idx >= len(self._stream_scripts):
            raise RuntimeError("stub ran out of stream scripts")
        script = self._stream_scripts[self._stream_idx]
        self._stream_idx += 1
        return _ReplayStream(script)

    async def create(self, **_kwargs: Any) -> Any:
        idx = self._create_idx
        self._create_idx += 1
        if idx < len(self._create_raises) and self._create_raises[idx] is not None:
            raise self._create_raises[idx]
        if idx >= len(self._create_responses):
            raise RuntimeError("stub ran out of create responses")
        text = self._create_responses[idx]

        class _Block:
            type = "text"

            def __init__(self, t: str) -> None:
                self.text = t

        class _Resp:
            def __init__(self, t: str) -> None:
                self.content = [_Block(t)]

        return _Resp(text)


class _HybridClient:
    def __init__(self, messages: _HybridMessages) -> None:
        self.messages = messages


class _SlowPlayback:
    def __init__(self, per_chunk_ms: float = 5.0) -> None:
        self._delay = per_chunk_ms / 1000.0
        self.played: list[bytes] = []
        self.sample_rate: int | None = None

    async def play(self, chunks: AsyncIterator[bytes], sample_rate: int) -> None:
        self.sample_rate = sample_rate
        async for chunk in chunks:
            self.played.append(chunk)
            if self._delay:
                await asyncio.sleep(self._delay)


async def _drive_one_turn(bus: AsyncEventBus, src) -> None:  # type: ignore[no-untyped-def]
    """Press + release, wait for TurnCompleted."""
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
    await asyncio.wait_for(done.wait(), timeout=5.0)
    waiter.cancel()
    try:
        await waiter
    except asyncio.CancelledError:
        pass


def _mk_daemon(
    *,
    store: MemoryStore,
    config: HerbertConfig,
    stt_text: str,
    messages: _HybridMessages,
):  # type: ignore[no-untyped-def]
    from herbert.hal.mock import MockEventSource

    src = MockEventSource()
    bus = AsyncEventBus()
    hal = Hal(
        platform="mock",
        event_source=src,
        audio_in=MockAudioIn(pcm=b"\x01\x00" * 800),
        audio_out=_SlowPlayback(),
    )

    def factory() -> Session:
        return SqliteSession(store, store.start_session())

    deps = DaemonDeps(
        config=config,
        bus=bus,
        hal=hal,
        stt=ReplaySttProvider(text=stt_text, duration_ms=200),
        tts=ReplayTtsProvider(chunks_per_sentence=2, chunk_bytes=64, per_chunk_ms=2),
        llm_client=_HybridClient(messages),
        persona="e2e persona",
        store=store,
        session_factory=factory,
    )
    return Daemon(deps), bus, src


class TestMemoryAcrossSessions:
    async def test_facts_and_summary_survive_into_next_session(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path)
        try:
            config = HerbertConfig(memory=MemoryConfig(db_path=db_path))
            messages = _HybridMessages(
                stream_scripts=[
                    # Session 1 assistant reply
                    [LlmDelta(t_ms=0, text="Nice to meet you. "), LlmDelta(t_ms=20, text="Welcome.\n")],
                    # Session 2 assistant reply
                    [LlmDelta(t_ms=0, text="Your name is Matt.\n")],
                ],
                create_responses=[
                    json.dumps(
                        {
                            "summary": "Matt introduced himself.",
                            "new_facts": ["User's name is Matt", "User lives in Monrovia"],
                        }
                    )
                ],
            )

            # --- session 1 ---
            daemon, bus, src = _mk_daemon(
                store=store,
                config=config,
                stt_text="My name is Matt and I live in Monrovia.",
                messages=messages,
            )
            runner = asyncio.create_task(daemon.run())
            try:
                await _drive_one_turn(bus, src)
                store.drain(timeout=2.0)

                # Simulate inactivity close
                await daemon._close_current_session()
                for t in list(daemon._extraction_tasks):
                    await t
                store.drain(timeout=2.0)

                # DB state after close
                assert "User's name is Matt" in store.get_facts()
                assert "User lives in Monrovia" in store.get_facts()
                summaries = store.get_recent_summaries(5)
                assert len(summaries) == 1
                assert summaries[0][0] == "Matt introduced himself."
            finally:
                await daemon.stop()
                try:
                    await asyncio.wait_for(runner, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass

            # --- session 2 (fresh daemon to prove persistence across
            # daemon restarts, not just within one process) ---
            daemon2, bus2, src2 = _mk_daemon(
                store=store,
                config=config,
                stt_text="what's my name?",
                messages=messages,  # same hybrid; next stream script fires
            )
            runner2 = asyncio.create_task(daemon2.run())
            try:
                await _drive_one_turn(bus2, src2)
                store.drain(timeout=2.0)

                # Assistant saw memory in its system prompt
                latest_system_prompt = messages.recorded_system[-1]
                assert "## What I know about Matt" in latest_system_prompt
                assert "User's name is Matt" in latest_system_prompt
                assert "User lives in Monrovia" in latest_system_prompt
                assert "## Recent sessions" in latest_system_prompt
                assert "Matt introduced himself" in latest_system_prompt

                # Session 2's message array does NOT include session 1 turns
                assert daemon2.session is not None
                live = [m.content for m in daemon2.session.messages]
                assert "My name is Matt and I live in Monrovia." not in live
                assert "what's my name?" in live
            finally:
                await daemon2.stop()
                try:
                    await asyncio.wait_for(runner2, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass

            # DB file ended the run in WAL mode — catches any accidental
            # pragma reset anywhere in the call graph.
            probe = sqlite3.connect(str(db_path))
            try:
                mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode.lower() == "wal"
            finally:
                probe.close()
        finally:
            store.close()

    async def test_extraction_failure_still_allows_next_session(
        self, tmp_path: Path
    ) -> None:
        """Extractor raises → session 1's summary stays null, but a fresh
        session 2 still starts cleanly with any prior facts intact."""
        db_path = tmp_path / "memory.db"
        store = MemoryStore(db_path)
        try:
            # Seed a pre-existing fact from an earlier (unrelated) session
            # to prove it survives.
            seed_sid = store.start_session()
            store.close_session(
                seed_sid,
                summary="old chat",
                new_facts=["Matt is a Dodgers fan"],
            )
            store.drain(timeout=2.0)

            config = HerbertConfig(memory=MemoryConfig(db_path=db_path))
            messages = _HybridMessages(
                stream_scripts=[
                    [LlmDelta(t_ms=0, text="Hi.\n")],  # session A
                    [LlmDelta(t_ms=0, text="Sure.\n")],  # session B (new daemon)
                ],
                create_responses=[],
                create_raises=[
                    RuntimeError("extractor boom"),
                    RuntimeError("extractor still boom"),
                ],
            )

            # --- session A: extraction fails ---
            daemon, bus, src = _mk_daemon(
                store=store,
                config=config,
                stt_text="short exchange",
                messages=messages,
            )
            runner = asyncio.create_task(daemon.run())
            try:
                await _drive_one_turn(bus, src)
                store.drain(timeout=2.0)

                await daemon._close_current_session()
                for t in list(daemon._extraction_tasks):
                    await t
                store.drain(timeout=2.0)

                # Pre-existing fact intact; no new summary for session A
                assert "Matt is a Dodgers fan" in store.get_facts()
                # The pre-existing ("old chat") summary is still visible
                summaries = store.get_recent_summaries(5)
                assert any("old chat" == s[0] for s in summaries)
            finally:
                await daemon.stop()
                try:
                    await asyncio.wait_for(runner, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass

            # --- session B: starts normally, sees the old fact ---
            daemon2, bus2, src2 = _mk_daemon(
                store=store,
                config=config,
                stt_text="hello again",
                messages=messages,
            )
            runner2 = asyncio.create_task(daemon2.run())
            try:
                await _drive_one_turn(bus2, src2)
                store.drain(timeout=2.0)

                latest_system_prompt = messages.recorded_system[-1]
                assert "Matt is a Dodgers fan" in latest_system_prompt
                assert "old chat" in latest_system_prompt
            finally:
                await daemon2.stop()
                try:
                    await asyncio.wait_for(runner2, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
        finally:
            store.close()
