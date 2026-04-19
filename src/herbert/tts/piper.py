"""Local Piper TTS — offline fallback when ElevenLabs is unavailable.

Piper is synchronous; we wrap each sentence in a thread call so the event
loop stays free. Per-sentence is the right granularity (not per-chunk)
because `synthesize_stream_raw` runs the ONNX model to completion before
yielding, so pretending we're async mid-generation would be dishonest.

Voice file lives at `~/.herbert/voices/en_US-lessac-medium.onnx` by default.
`scripts/fetch-models.py` will download it in a future iteration; for now
the path is configured and we fail-loud if missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from herbert.tts import TtsState

log = logging.getLogger(__name__)


class PiperVoiceMissingError(FileNotFoundError):
    """Raised when the configured Piper voice file does not exist."""


def _read_sample_rate(voice_path: Path) -> int | None:
    """Read the sample rate from a Piper voice's JSON sidecar without loading ONNX.

    Piper voices ship `<voice>.onnx` + `<voice>.onnx.json`; the sidecar has an
    `audio.sample_rate` field we can read synchronously. Returns None if the
    sidecar isn't present — the full `PiperVoice.load` path will populate the
    rate at first `stream()` call in that case.
    """
    sidecar = voice_path.with_suffix(voice_path.suffix + ".json")
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text())
        return int(data["audio"]["sample_rate"])
    except (KeyError, ValueError, json.JSONDecodeError):
        log.warning("piper voice sidecar %s missing audio.sample_rate", sidecar)
        return None


class PiperProvider:
    """`TtsProvider` backed by a local Piper ONNX model."""

    def __init__(self, voice_path: Path) -> None:
        self._voice_path = voice_path
        self._voice: Any | None = None
        self._load_lock = asyncio.Lock()
        # Parse the sample rate from the sidecar at construction time so
        # callers (e.g. AudioOut.play) can read `sample_rate` before the
        # first `stream()` triggers the ONNX load.
        self._sample_rate: int | None = (
            _read_sample_rate(voice_path) if voice_path.exists() else None
        )

    @property
    def sample_rate(self) -> int:
        if self._sample_rate is None:
            raise RuntimeError(
                f"piper voice sample rate unknown. "
                f"Missing sidecar JSON at {self._voice_path.with_suffix(self._voice_path.suffix + '.json')}? "
                "Re-run: uv run python scripts/fetch-models.py --voice en_US-lessac-medium"
            )
        return self._sample_rate

    async def stream(
        self,
        sentences: AsyncIterator[str],
        state: TtsState | None = None,
    ) -> AsyncIterator[bytes]:
        await self._ensure_loaded()
        start = time.perf_counter()

        async for sentence in sentences:
            if not sentence.strip():
                continue
            sent_start = time.perf_counter()
            chunks: list[bytes] = await asyncio.to_thread(self._synthesize, sentence)
            ttfb_ms = int((sent_start - start) * 1000)
            if state is not None and not chunks and state.ttfb_ms is None:
                pass  # no output for this sentence; nothing to record yet
            for idx, chunk in enumerate(chunks):
                if not chunk:
                    continue
                if state is not None:
                    state.chunks_produced += 1
                    state.bytes_produced += len(chunk)
                    if idx == 0:
                        if state.ttfb_ms is None:
                            state.ttfb_ms = ttfb_ms
                        state.per_sentence_ttfb_ms.append(ttfb_ms)
                yield chunk
            if state is not None and chunks:
                state.sentences_consumed += 1

    async def _ensure_loaded(self) -> None:
        if self._voice is not None:
            return
        async with self._load_lock:
            if self._voice is not None:
                return
            if not self._voice_path.exists():
                raise PiperVoiceMissingError(
                    f"Piper voice not found at {self._voice_path}. "
                    "Download a .onnx (+ matching .onnx.json) from "
                    "github.com/rhasspy/piper/blob/master/VOICES.md "
                    "into ~/.herbert/voices/."
                )
            log.info("loading piper voice from %s", self._voice_path)
            self._voice = await asyncio.to_thread(self._load_voice)
            # Sidecar already set this at init; fall back to voice config if not.
            if self._sample_rate is None:
                self._sample_rate = self._voice.config.sample_rate
            log.info("piper voice loaded; sample_rate=%d", self._sample_rate)

    def _load_voice(self) -> Any:
        from piper import PiperVoice

        return PiperVoice.load(str(self._voice_path))

    def _synthesize(self, sentence: str) -> list[bytes]:
        """Run Piper synthesize() and return raw int16 PCM chunks.

        Piper's current API yields `AudioChunk` objects per sentence with
        `.audio_int16_bytes` carrying the raw PCM. (Older piper-tts exposed
        `synthesize_stream_raw` returning bytes directly; this wrapper keeps
        the downstream contract stable across versions.)
        """
        assert self._voice is not None
        return [chunk.audio_int16_bytes for chunk in self._voice.synthesize(sentence)]
