"""Live ElevenLabs streaming smoke test. Gated by HERBERT_LIVE=1."""

from __future__ import annotations

import os

import pytest

from herbert.tts import TtsState
from herbert.tts.elevenlabs_stream import ElevenLabsProvider

pytestmark = pytest.mark.live


async def _iter(items):  # type: ignore[no-untyped-def]
    for item in items:
        yield item


@pytest.mark.skipif(
    os.environ.get("HERBERT_LIVE") != "1"
    or not os.environ.get("ELEVENLABS_API_KEY")
    or not os.environ.get("ELEVENLABS_VOICE_ID"),
    reason="set HERBERT_LIVE=1 + ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID to exercise live WS",
)
async def test_streams_pcm_from_real_endpoint() -> None:
    provider = ElevenLabsProvider(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        voice_id=os.environ["ELEVENLABS_VOICE_ID"],
    )
    state = TtsState()
    total = 0
    async for chunk in provider.stream(_iter(["Hello, Herbert."]), state=state):
        total += len(chunk)
    # A single short sentence should produce at least a few KB of 16kHz int16 PCM
    assert total > 2000
    assert state.ttfb_ms is not None
    # Loose latency bar, not the R6 target
    assert state.ttfb_ms < 2000
