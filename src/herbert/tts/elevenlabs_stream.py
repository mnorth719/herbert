"""ElevenLabs streaming TTS over the `stream-input` WebSocket.

We bypass the `elevenlabs` Python SDK's higher-level helpers and talk to the
documented WebSocket endpoint directly:

    wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
        ?model_id=eleven_flash_v2_5&output_format=pcm_16000

Message protocol (JSON):
  send    {"text": " ", "voice_settings": {...}}        # init frame (space)
  send    {"text": "Hello there. ", "try_trigger_generation": true}
  send    {"text": ""}                                    # flush/close
  recv    {"audio": "<base64 pcm>", "isFinal": false}
  recv    {"audio": null, "isFinal": true}                # end-of-stream

Output is pcm_16000 — 16-bit little-endian mono 16kHz PCM — matching our
AudioOut expectations. `eleven_flash_v2_5` is the latency-optimised model
(~75-150ms TTFB) that keeps R6's 300ms TTS-first-chunk ceiling achievable.

Design notes:
- A fresh WS is opened per `stream()` call. ElevenLabs does not support
  multi-context streams on the non-multi endpoint; we accept the ~50ms
  handshake cost per turn rather than multiplex.
- On WS disconnect mid-sentence the caller sees the exception propagate;
  the state machine (Unit 7) classifies it as `tts_error` and parks in
  `error` state per R16.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

from herbert.tts import TtsState

log = logging.getLogger(__name__)

_ENDPOINT = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
_SAMPLE_RATE = 16000  # pcm_16000 output format


class ElevenLabsProvider:
    """Streaming ElevenLabs WebSocket `TtsProvider`."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "pcm_16000",
        chunk_length_schedule: list[int] | None = None,
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._output_format = output_format
        # Research note: small initial values → fast first chunk; growing →
        # fewer round-trips once we're mid-response
        self._chunk_length_schedule = chunk_length_schedule or [50, 90, 120, 150, 200]

    @property
    def sample_rate(self) -> int:
        return _SAMPLE_RATE

    async def stream(
        self,
        sentences: AsyncIterator[str],
        state: TtsState | None = None,
    ) -> AsyncIterator[bytes]:
        ws = await self._connect()
        start = time.perf_counter()

        # Init frame: a single space primes the session + sets voice settings
        await ws.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                    "generation_config": {
                        "chunk_length_schedule": self._chunk_length_schedule,
                    },
                    "xi_api_key": self._api_key,
                }
            )
        )

        async def _sender() -> None:
            async for sentence in sentences:
                if not sentence.strip():
                    continue
                # Trailing space signals end-of-sentence to the server
                text = sentence if sentence.endswith(" ") else sentence + " "
                await ws.send(json.dumps({"text": text, "try_trigger_generation": True}))
                if state is not None:
                    state.sentences_consumed += 1
            # Empty text frame closes the session cleanly
            await ws.send(json.dumps({"text": ""}))

        sender_task = asyncio.create_task(_sender())
        try:
            async for chunk in self._receive(ws, start, state):
                yield chunk
            # Receiver ended normally (isFinal or stream exhausted) — let the
            # sender finish pushing whatever remains so session-level state
            # (e.g. state.sentences_consumed) reflects everything submitted.
            try:
                await sender_task
            except Exception as exc:
                log.warning("elevenlabs sender errored after receive ended: %s", exc)
        except BaseException:
            sender_task.cancel()
            with _ignore_cancelled():
                await sender_task
            raise
        finally:
            await ws.close()

    async def _connect(self) -> Any:
        import websockets  # type: ignore[import-untyped]

        url = _ENDPOINT.format(voice_id=self._voice_id) + "?" + urlencode(
            {"model_id": self._model_id, "output_format": self._output_format}
        )
        return await websockets.connect(url)

    async def _receive(
        self,
        ws: Any,
        start: float,
        state: TtsState | None,
    ) -> AsyncIterator[bytes]:
        per_sentence_counter = 0
        async for raw in ws:
            message = _parse_message(raw)
            if message is None:
                continue
            if message.get("error"):
                # ElevenLabs surfaces auth / voice-not-found / quota errors inline
                raise ElevenLabsError(message.get("error") or "unknown error")

            audio_b64 = message.get("audio")
            if audio_b64:
                chunk = base64.b64decode(audio_b64)
                if state is not None:
                    state.chunks_produced += 1
                    state.bytes_produced += len(chunk)
                    if state.ttfb_ms is None:
                        state.ttfb_ms = int((time.perf_counter() - start) * 1000)
                    # First chunk of each sentence — record its TTFB.
                    # ElevenLabs doesn't mark sentence boundaries explicitly; we
                    # approximate by counting one TTFB per "isFinal=false, audio
                    # after a quiet gap" edge is overkill here, so we just
                    # record one per distinct audio event.
                    if per_sentence_counter == 0:
                        state.per_sentence_ttfb_ms.append(state.ttfb_ms)
                    per_sentence_counter += 1
                yield chunk

            if message.get("isFinal"):
                # Server signalled end-of-stream; break out of the receive loop
                return


def _parse_message(raw: Any) -> dict[str, Any] | None:
    try:
        if isinstance(raw, bytes):
            return json.loads(raw.decode("utf-8"))
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("elevenlabs: could not decode WS message")
        return None


class ElevenLabsError(RuntimeError):
    """Server-side error reported inline on the WS (auth, quota, voice)."""


class _ignore_cancelled:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return exc_type is asyncio.CancelledError
