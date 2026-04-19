"""Speech-to-Text provider Protocol + common result type.

The Protocol accepts 16kHz 16-bit mono PCM and yields a `SttResult` with
the transcribed text and the wall-clock time spent. Per-stage ceiling
tracking (the "stt" bucket in `TurnSpan`) consumes `duration_ms` — the
provider itself never publishes latency events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SttResult:
    """Transcription output + the wall-clock spent producing it."""

    text: str
    duration_ms: int


@runtime_checkable
class SttProvider(Protocol):
    """Stateless speech-to-text. Implementations are safe to share across turns."""

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> SttResult: ...


__all__ = ["SttProvider", "SttResult"]
