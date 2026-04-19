"""Latency instrumentation + error recovery monitor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from herbert.events import ExchangeLatency
from herbert.turn import R6_CEILINGS, TurnSpan

# Re-use the daemon plumbing from the pipeline test
from tests.integration.test_daemon_pipeline import _build_daemon


async def _collect_events(bus, types, timeout=2.0):
    collected = []

    async def _loop() -> None:
        async with bus.subscribe() as sub:
            while True:
                event = await sub.receive()
                if isinstance(event, types):
                    collected.append(event)

    task = asyncio.create_task(_loop())
    await asyncio.sleep(timeout)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return collected


class TestTurnSpanEvaluate:
    def test_no_miss_when_under_ceilings(self) -> None:
        span = TurnSpan(turn_id="t")
        span.record("stt", 500)
        span.record("llm_ttft", 400)
        span.total_ms = 2000
        misses = span.evaluate_ceilings("mac_hybrid")
        assert misses == []
        assert span.misses == []

    def test_misses_captured_per_stage(self) -> None:
        span = TurnSpan(turn_id="t")
        span.record("stt", 3000)  # over mac_hybrid stt ceiling (1500)
        span.record("llm_ttft", 5000)  # over mac_hybrid llm_ttft (1000)
        span.total_ms = 10000  # over total (3500)
        misses = span.evaluate_ceilings("mac_hybrid")
        stages = {m[0] for m in misses}
        assert stages == {"stt", "llm_ttft", "total"}
        assert set(span.misses) == {"stt", "llm_ttft", "total"}

    def test_unknown_mode_no_misses(self) -> None:
        span = TurnSpan(turn_id="t")
        span.record("stt", 99999)
        span.total_ms = 99999
        assert span.evaluate_ceilings("unknown_mode") == []

    def test_mac_hybrid_ceilings_present(self) -> None:
        assert "stt" in R6_CEILINGS["mac_hybrid"]
        assert R6_CEILINGS["mac_hybrid"]["stt"] > 0


class TestExchangeLatencyEmission:
    async def test_happy_path_emits_exchange_latency(self) -> None:
        daemon, bus, src, _aout, _ = _build_daemon(["Hello there. "])

        events = []

        async def _collect() -> None:
            async with bus.subscribe() as sub:
                while True:
                    events.append(await sub.receive())

        collector = asyncio.create_task(_collect())
        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()

        # Wait for turn to complete
        for _ in range(100):
            await asyncio.sleep(0.02)
            if daemon.state == "idle" and any(
                isinstance(e, ExchangeLatency) for e in events
            ):
                break

        exchange_events = [e for e in events if isinstance(e, ExchangeLatency)]
        assert len(exchange_events) == 1
        evt = exchange_events[0]
        assert evt.total_ms > 0
        assert evt.mode == "mac_hybrid"

        collector.cancel()
        try:
            await collector
        except asyncio.CancelledError:
            pass
        await daemon.stop()
        await src.close()
        await runner


class TestRecoveryMonitor:
    async def test_retryable_error_probes_network_and_recovers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Timeout(Exception):
            pass

        daemon, _bus, src, _aout, _ = _build_daemon(
            [], llm_error=_Timeout("connection timeout")
        )

        # Probe returns True on the first check → recovery fires fast.
        probe = AsyncMock(return_value=True)
        monkeypatch.setattr(daemon, "_probe_network_ok", probe)
        # Shrink the delays so the test runs quickly
        import herbert.daemon as daemon_mod

        async def _fast_monitor(self, turn_id: str) -> None:
            await asyncio.sleep(0.05)
            if self._state.state != "error":
                return
            if await self._probe_network_ok():
                await self._state.transition("idle", turn_id=turn_id)

        monkeypatch.setattr(daemon_mod.Daemon, "_monitor_recovery", _fast_monitor)

        runner = asyncio.create_task(daemon.run())

        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        # Wait for the error state
        for _ in range(50):
            await asyncio.sleep(0.02)
            if daemon.state == "error":
                break
        assert daemon.state == "error"
        # Wait for recovery monitor
        for _ in range(50):
            await asyncio.sleep(0.02)
            if daemon.state == "idle":
                break
        assert daemon.state == "idle"
        assert probe.await_count >= 1

        await daemon.stop()
        await src.close()
        await runner

    async def test_non_retryable_error_does_not_start_monitor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _AuthErr(Exception):
            pass

        daemon, _bus, src, _aout, _ = _build_daemon(
            [], llm_error=_AuthErr("invalid_api_key authentication")
        )
        probe = AsyncMock(return_value=True)
        monkeypatch.setattr(daemon, "_probe_network_ok", probe)

        runner = asyncio.create_task(daemon.run())
        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        for _ in range(50):
            await asyncio.sleep(0.02)
            if daemon.state == "error":
                break
        assert daemon.state == "error"
        # Give any potential monitor time to run — it shouldn't
        await asyncio.sleep(0.2)
        assert probe.await_count == 0
        assert daemon.state == "error"  # still terminal

        await daemon.stop()
        await src.close()
        await runner

    async def test_press_cancels_pending_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Timeout(Exception):
            pass

        daemon, _bus, src, _aout, _ = _build_daemon(
            [], llm_error=_Timeout("connection reset")
        )
        # Mock a probe that never resolves so recovery would be stuck
        probe = AsyncMock(return_value=False)
        monkeypatch.setattr(daemon, "_probe_network_ok", probe)

        runner = asyncio.create_task(daemon.run())
        await src.press()
        await asyncio.sleep(0.02)
        await src.release()
        for _ in range(50):
            await asyncio.sleep(0.02)
            if daemon.state == "error":
                break
        # Recovery task is pending now
        assert daemon._recovery_task is not None
        # User presses again — recovery should get cancelled
        await src.press()
        await asyncio.sleep(0.05)
        assert daemon._recovery_task.cancelled() or daemon._recovery_task.done()

        await daemon.stop()
        await src.close()
        await runner
