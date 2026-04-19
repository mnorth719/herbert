"""PiperProvider unit tests — `piper.PiperVoice.load` is monkeypatched."""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from herbert.tts import TtsProvider, TtsState
from herbert.tts.piper import PiperProvider, PiperVoiceMissingError


class _FakeConfig:
    def __init__(self, sample_rate: int = 22050) -> None:
        self.sample_rate = sample_rate


class _FakeVoice:
    def __init__(self, sample_rate: int = 22050) -> None:
        self.config = _FakeConfig(sample_rate)
        self._calls: list[str] = []

    @staticmethod
    def load(path: str) -> _FakeVoice:
        return _FakeVoice()

    def synthesize_stream_raw(self, sentence: str):  # type: ignore[no-untyped-def]
        self._calls.append(sentence)
        # Emit 2 chunks per sentence so per-chunk behavior is observable
        yield b"A" * 1000
        yield b"B" * 500


def _install_piper_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("piper")
    module.PiperVoice = _FakeVoice  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", module)


async def _iter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


class TestPiperProvider:
    def test_satisfies_tts_provider_protocol(self, tmp_path: Path) -> None:
        p = PiperProvider(tmp_path / "missing.onnx")
        assert isinstance(p, TtsProvider)

    async def test_missing_voice_file_raises(self, tmp_path: Path) -> None:
        p = PiperProvider(tmp_path / "missing.onnx")
        with pytest.raises(PiperVoiceMissingError) as excinfo:
            async for _ in p.stream(_iter(["hello"])):
                pass
        assert "~/.herbert/voices" in str(excinfo.value)

    async def test_streams_pcm_for_one_sentence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_piper_stub(monkeypatch)
        voice_file = tmp_path / "voice.onnx"
        voice_file.write_bytes(b"fake")
        p = PiperProvider(voice_file)

        state = TtsState()
        chunks = []
        async for chunk in p.stream(_iter(["Hello there."]), state=state):
            chunks.append(chunk)

        assert chunks == [b"A" * 1000, b"B" * 500]
        assert state.chunks_produced == 2
        assert state.bytes_produced == 1500
        assert state.sentences_consumed == 1
        assert state.ttfb_ms is not None and state.ttfb_ms >= 0
        assert len(state.per_sentence_ttfb_ms) == 1
        assert p.sample_rate == 22050

    async def test_empty_sentence_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_piper_stub(monkeypatch)
        voice_file = tmp_path / "voice.onnx"
        voice_file.write_bytes(b"fake")
        p = PiperProvider(voice_file)

        state = TtsState()
        chunks = [c async for c in p.stream(_iter(["", "  ", "\n"]), state=state)]
        assert chunks == []
        assert state.sentences_consumed == 0

    async def test_multiple_sentences_record_separate_ttfbs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_piper_stub(monkeypatch)
        voice_file = tmp_path / "voice.onnx"
        voice_file.write_bytes(b"fake")
        p = PiperProvider(voice_file)

        state = TtsState()
        _ = [c async for c in p.stream(_iter(["One.", "Two.", "Three."]), state=state)]
        assert state.sentences_consumed == 3
        assert len(state.per_sentence_ttfb_ms) == 3
        # Each successive sentence's TTFB should be monotonically nondecreasing
        assert state.per_sentence_ttfb_ms == sorted(state.per_sentence_ttfb_ms)

    def test_sample_rate_readable_before_first_stream(self, tmp_path: Path) -> None:
        """AudioOut.play() reads sample_rate before stream() fires; sidecar must cover it."""
        voice_file = tmp_path / "voice.onnx"
        voice_file.write_bytes(b"fake")
        sidecar = tmp_path / "voice.onnx.json"
        sidecar.write_text('{"audio": {"sample_rate": 22050}}')

        p = PiperProvider(voice_file)
        # No stream() yet — would have raised before the sidecar fix
        assert p.sample_rate == 22050

    def test_sample_rate_errors_helpfully_when_sidecar_missing(self, tmp_path: Path) -> None:
        voice_file = tmp_path / "voice.onnx"
        voice_file.write_bytes(b"fake")
        # No sidecar next to it
        p = PiperProvider(voice_file)
        with pytest.raises(RuntimeError, match=r"fetch-models\.py"):
            _ = p.sample_rate

    async def test_voice_load_is_once(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        loads = 0

        class _Counting(_FakeVoice):
            @staticmethod
            def load(path: str) -> _Counting:  # type: ignore[override]
                nonlocal loads
                loads += 1
                return _Counting()

        module = types.ModuleType("piper")
        module.PiperVoice = _Counting  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "piper", module)

        voice_file = tmp_path / "voice.onnx"
        voice_file.write_bytes(b"fake")
        p = PiperProvider(voice_file)

        _ = [c async for c in p.stream(_iter(["One."]))]
        _ = [c async for c in p.stream(_iter(["Two."]))]
        assert loads == 1
