"""Whisper integration test against the `hello-world` fixture WAV.

Gated behind `HERBERT_LIVE_WHISPER=1` because it requires:
  - the `pywhispercpp` native wheel to be installed
  - the whisper model file at `~/.herbert/models/ggml-base.en-q5_1.bin`

Skipped by default so macOS dev + CI don't need the model present.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import pytest

from herbert.stt.whisper_cpp import WhisperCppProvider

pytestmark = pytest.mark.live

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "turns" / "hello-world"


def _read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as f:
        return f.readframes(f.getnframes()), f.getframerate()


@pytest.mark.skipif(
    os.environ.get("HERBERT_LIVE_WHISPER") != "1",
    reason="set HERBERT_LIVE_WHISPER=1 to exercise real whisper.cpp",
)
async def test_transcribe_hello_world() -> None:
    model_path = Path.home() / ".herbert" / "models" / "ggml-base.en-q5_1.bin"
    if not model_path.exists():
        pytest.skip(f"model not present at {model_path}; run scripts/fetch-models.py")

    pcm, sr = _read_wav(_FIXTURE / "input.wav")
    provider = WhisperCppProvider(model_path)
    result = await provider.transcribe(pcm, sample_rate=sr)
    # Placeholder input.wav is silent; we only assert the call completes.
    # A real "hello herbert" recording — once captured — should assert a
    # loose match against the fixture's stt.json ground-truth.
    assert isinstance(result.text, str)
    assert result.duration_ms >= 0
