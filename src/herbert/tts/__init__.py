"""Text-to-Speech provider Protocol + factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class TtsState:
    """Mutable per-turn timing tracking written by provider `.stream()`.

    `ttfb_ms` is the wall-clock from `stream()` invocation to the first
    non-empty PCM chunk (what the plan calls `tts_ttfb` in R6). Per-sentence
    TTFB values are appended so the orchestrator can detect gaps between
    sentences.
    """

    ttfb_ms: int | None = None
    bytes_produced: int = 0
    chunks_produced: int = 0
    sentences_consumed: int = 0
    per_sentence_ttfb_ms: list[int] = field(default_factory=list)


@runtime_checkable
class TtsProvider(Protocol):
    """Streaming text-to-speech. Yields 16-bit mono PCM at `sample_rate`."""

    @property
    def sample_rate(self) -> int: ...

    async def stream(
        self,
        sentences: AsyncIterator[str],
        state: TtsState | None = None,
    ) -> AsyncIterator[bytes]: ...


__all__ = ["TtsProvider", "TtsState"]
