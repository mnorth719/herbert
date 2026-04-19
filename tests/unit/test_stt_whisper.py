"""WhisperCppProvider unit tests — model loading is mocked."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from herbert.stt import SttProvider, SttResult
from herbert.stt.whisper_cpp import WhisperCppProvider, WhisperModelMissingError


def _install_pywhispercpp_stub(monkeypatch: pytest.MonkeyPatch, transcribe_fn) -> None:  # type: ignore[no-untyped-def]
    """Install a fake `pywhispercpp.model.Model` that delegates to `transcribe_fn`."""

    class _FakeModel:
        def __init__(self, *args, **kwargs) -> None:
            self._args = args
            self._kwargs = kwargs

        def transcribe(self, audio):  # type: ignore[no-untyped-def]
            return transcribe_fn(audio)

    module = types.ModuleType("pywhispercpp.model")
    module.Model = _FakeModel  # type: ignore[attr-defined]
    pkg = types.ModuleType("pywhispercpp")
    pkg.model = module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pywhispercpp", pkg)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", module)


def _segment(text: str) -> MagicMock:
    seg = MagicMock()
    seg.text = text
    return seg


class TestWhisperCpp:
    def test_satisfies_stt_provider_protocol(self) -> None:
        provider = WhisperCppProvider(Path("/nonexistent"))
        assert isinstance(provider, SttProvider)

    async def test_missing_model_raises_with_helpful_message(self, tmp_path: Path) -> None:
        provider = WhisperCppProvider(tmp_path / "not-there.bin")
        with pytest.raises(WhisperModelMissingError) as excinfo:
            await provider.transcribe(b"\x00\x01" * 16000, sample_rate=16000)
        msg = str(excinfo.value)
        assert "fetch-models.py" in msg
        assert "base.en-q5_1" in msg

    async def test_empty_pcm_returns_empty_result_without_loading_model(
        self, tmp_path: Path
    ) -> None:
        provider = WhisperCppProvider(tmp_path / "not-there.bin")
        result = await provider.transcribe(b"", sample_rate=16000)
        assert result == SttResult(text="", duration_ms=0)

    async def test_sample_rate_mismatch_errors(self, tmp_path: Path) -> None:
        provider = WhisperCppProvider(tmp_path / "not-there.bin")
        with pytest.raises(ValueError, match="16000Hz"):
            await provider.transcribe(b"\x00\x00", sample_rate=48000)

    async def test_transcribe_returns_concatenated_segments(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        model_path = tmp_path / "ggml-base.en-q5_1.bin"
        model_path.write_bytes(b"fake-model")
        _install_pywhispercpp_stub(
            monkeypatch, lambda audio: [_segment(" hello"), _segment(" herbert")]
        )

        provider = WhisperCppProvider(model_path)
        result = await provider.transcribe(b"\x01\x00" * 16000, sample_rate=16000)
        assert result.text == "hello herbert"
        assert result.duration_ms >= 0

    async def test_model_load_is_once_under_concurrency(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        model_path = tmp_path / "ggml-base.en-q5_1.bin"
        model_path.write_bytes(b"fake-model")
        load_count = 0

        class _SlowModel:
            def __init__(self, *args, **kwargs) -> None:
                nonlocal load_count
                load_count += 1
                # Amplify the race window
                import time as _t

                _t.sleep(0.02)

            def transcribe(self, audio):  # type: ignore[no-untyped-def]
                return [_segment("ok")]

        module = types.ModuleType("pywhispercpp.model")
        module.Model = _SlowModel  # type: ignore[attr-defined]
        pkg = types.ModuleType("pywhispercpp")
        pkg.model = module  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pywhispercpp", pkg)
        monkeypatch.setitem(sys.modules, "pywhispercpp.model", module)

        provider = WhisperCppProvider(model_path)
        await asyncio.gather(
            provider.transcribe(b"\x00\x01" * 8000, sample_rate=16000),
            provider.transcribe(b"\x00\x01" * 8000, sample_rate=16000),
            provider.transcribe(b"\x00\x01" * 8000, sample_rate=16000),
        )
        assert load_count == 1

    async def test_duration_is_measured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        model_path = tmp_path / "model.bin"
        model_path.write_bytes(b"fake")

        def slow_transcribe(audio):  # type: ignore[no-untyped-def]
            import time as _t

            _t.sleep(0.05)
            return [_segment("x")]

        _install_pywhispercpp_stub(monkeypatch, slow_transcribe)
        provider = WhisperCppProvider(model_path)
        result = await provider.transcribe(b"\x00\x00" * 1000, sample_rate=16000)
        assert result.duration_ms >= 50
