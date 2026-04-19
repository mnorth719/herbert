"""ElevenLabsProvider unit tests — WebSocket + elevenlabs API are mocked."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from herbert.tts import TtsProvider, TtsState
from herbert.tts.elevenlabs_stream import ElevenLabsError, ElevenLabsProvider


class _FakeWs:
    """Playback-only WebSocket double: yields scripted server frames.

    Yields control between frames so the concurrent sender task has chances
    to run. Without this, the receiver would drain every scripted frame
    synchronously before the sender processes a single sentence.
    """

    def __init__(self, server_frames: list[dict[str, Any]]) -> None:
        self._server_frames = list(server_frames)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            for frame in self._server_frames:
                # Cede control so the sender task can run between server frames
                await asyncio.sleep(0)
                yield json.dumps(frame)

        return _gen()


async def _iter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


def _pcm_frame(payload: bytes, is_final: bool = False) -> dict[str, Any]:
    return {
        "audio": base64.b64encode(payload).decode("ascii"),
        "isFinal": is_final,
    }


@pytest.fixture
def patch_connect(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Replace ElevenLabsProvider._connect with a factory returning _FakeWs."""

    def _install(frames: list[dict[str, Any]]) -> _FakeWs:
        ws = _FakeWs(frames)

        async def _fake_connect(self: ElevenLabsProvider) -> Any:
            return ws

        monkeypatch.setattr(ElevenLabsProvider, "_connect", _fake_connect)
        return ws

    return _install


class TestElevenLabsProvider:
    def test_satisfies_tts_provider_protocol(self) -> None:
        p = ElevenLabsProvider(api_key="k", voice_id="v")
        assert isinstance(p, TtsProvider)
        assert p.sample_rate == 16000

    async def test_streams_pcm_from_server_frames(self, patch_connect) -> None:  # type: ignore[no-untyped-def]
        ws = patch_connect(
            [
                _pcm_frame(b"HELLO"),
                _pcm_frame(b"WORLD"),
                {"audio": None, "isFinal": True},
            ]
        )
        p = ElevenLabsProvider(api_key="k", voice_id="v")
        state = TtsState()
        chunks = []
        async for chunk in p.stream(_iter(["Hello there."]), state=state):
            chunks.append(chunk)

        assert chunks == [b"HELLO", b"WORLD"]
        assert state.chunks_produced == 2
        assert state.bytes_produced == 10
        assert state.sentences_consumed == 1
        assert state.ttfb_ms is not None and state.ttfb_ms >= 0
        assert ws.closed is True

    async def test_init_frame_sends_api_key_and_voice_settings(
        self, patch_connect
    ) -> None:  # type: ignore[no-untyped-def]
        ws = patch_connect([{"audio": None, "isFinal": True}])
        p = ElevenLabsProvider(api_key="sk-fake", voice_id="v")
        async for _ in p.stream(_iter(["Hi."])):
            pass
        # First message is the init: space-only text + voice settings + api key
        assert ws.sent[0]["text"] == " "
        assert ws.sent[0]["xi_api_key"] == "sk-fake"
        assert "voice_settings" in ws.sent[0]
        assert "generation_config" in ws.sent[0]

    async def test_sentence_frame_has_trailing_space(
        self, patch_connect
    ) -> None:  # type: ignore[no-untyped-def]
        ws = patch_connect([{"audio": None, "isFinal": True}])
        p = ElevenLabsProvider(api_key="k", voice_id="v")
        async for _ in p.stream(_iter(["Hello there."])):
            pass
        sentence_frames = [f for f in ws.sent if f["text"] not in (" ", "")]
        assert sentence_frames == [{"text": "Hello there. ", "try_trigger_generation": True}]

    async def test_close_frame_sent_after_sentences(
        self, patch_connect
    ) -> None:  # type: ignore[no-untyped-def]
        ws = patch_connect([{"audio": None, "isFinal": True}])
        p = ElevenLabsProvider(api_key="k", voice_id="v")
        async for _ in p.stream(_iter(["Hello."])):
            pass
        assert ws.sent[-1] == {"text": ""}

    async def test_empty_sentence_not_sent(self, patch_connect) -> None:  # type: ignore[no-untyped-def]
        ws = patch_connect([{"audio": None, "isFinal": True}])
        p = ElevenLabsProvider(api_key="k", voice_id="v")
        async for _ in p.stream(_iter(["", "  "])):
            pass
        sentence_frames = [f for f in ws.sent if f["text"] not in (" ", "")]
        assert sentence_frames == []

    async def test_server_error_raises(self, patch_connect) -> None:  # type: ignore[no-untyped-def]
        patch_connect([{"error": "unauthorized"}, {"audio": None, "isFinal": True}])
        p = ElevenLabsProvider(api_key="bad", voice_id="v")
        with pytest.raises(ElevenLabsError, match="unauthorized"):
            async for _ in p.stream(_iter(["Hi."])):
                pass

    async def test_final_message_ends_stream(self, patch_connect) -> None:  # type: ignore[no-untyped-def]
        patch_connect(
            [
                _pcm_frame(b"A"),
                {"audio": None, "isFinal": True},
                _pcm_frame(b"SHOULD_NOT_APPEAR"),
            ]
        )
        p = ElevenLabsProvider(api_key="k", voice_id="v")
        chunks = [c async for c in p.stream(_iter(["Hi."]))]
        assert chunks == [b"A"]
