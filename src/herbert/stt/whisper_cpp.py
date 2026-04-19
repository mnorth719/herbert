"""Local whisper.cpp STT via `pywhispercpp`.

Design notes:

- `pywhispercpp` is synchronous. We wrap it with `asyncio.to_thread` so the
  event loop stays free for audio capture + playback during transcription.
- Model loading is lazy-and-once. First `transcribe` pays the load cost; the
  load is serialised behind an `asyncio.Lock` so a burst of early turns does
  not double-load the model.
- Empty PCM in → empty text out. The plan calls this out as a test case;
  whisper would otherwise raise or produce garbage on a zero-length input.
- Sample-rate mismatch is a hard error. Resampling belongs upstream (in the
  AudioIn adapter) where the PCM already sits in numpy; we refuse to do it
  implicitly in the STT boundary.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from herbert.stt import SttResult

log = logging.getLogger(__name__)

# 16kHz is whisper.cpp's native sample rate. Other rates require resampling,
# which we intentionally push upstream rather than silently doing here.
WHISPER_SAMPLE_RATE = 16000


class WhisperModelMissingError(FileNotFoundError):
    """Raised when the configured model file does not exist on disk."""


class WhisperCppProvider:
    """Async `SttProvider` backed by a local `pywhispercpp` model.

    `n_threads=4` matches the Pi 5 Cortex-A76 core count; on Mac it is a safe
    lower bound that avoids starving the audio thread during turns.
    """

    def __init__(
        self,
        model_path: Path,
        n_threads: int = 4,
    ) -> None:
        self._model_path = model_path
        self._n_threads = n_threads
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> SttResult:
        if sample_rate != WHISPER_SAMPLE_RATE:
            raise ValueError(
                f"whisper.cpp requires {WHISPER_SAMPLE_RATE}Hz mono PCM, got {sample_rate}Hz. "
                "Resample upstream (in AudioIn) before handing audio to the STT provider."
            )
        if not pcm:
            return SttResult(text="", duration_ms=0)

        await self._ensure_loaded()
        start = time.perf_counter()
        text = await asyncio.to_thread(self._transcribe_sync, pcm)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return SttResult(text=text.strip(), duration_ms=duration_ms)

    async def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return
            if not self._model_path.exists():
                raise WhisperModelMissingError(
                    f"Whisper model not found at {self._model_path}. "
                    "Run `python scripts/fetch-models.py` to download "
                    "ggml-base.en-q5_1.bin into ~/.herbert/models/."
                )
            log.info("loading whisper model from %s", self._model_path)
            self._model = await asyncio.to_thread(self._load_model)
            log.info("whisper model loaded")

    def _load_model(self) -> Any:
        from pywhispercpp.model import Model

        return Model(
            str(self._model_path),
            n_threads=self._n_threads,
            no_context=True,
            single_segment=True,
            print_progress=False,
            print_realtime=False,
        )

    def _transcribe_sync(self, pcm: bytes) -> str:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        assert self._model is not None  # _ensure_loaded guarantees this
        segments = self._model.transcribe(audio)
        return "".join(getattr(s, "text", "") for s in segments)
