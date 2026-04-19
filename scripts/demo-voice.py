#!/usr/bin/env python3
"""End-to-end voice loop smoke — everything Unit 3-6 has delivered, wired up.

Hold SPACEBAR (or use --seconds), speak a prompt, Herbert replies out loud.

  uv run scripts/demo-voice.py
  uv run scripts/demo-voice.py --seconds 4
  uv run scripts/demo-voice.py --text "What's the capital of France?"

Prereqs:
  - ~/.herbert/secrets.env with ANTHROPIC_API_KEY and ELEVENLABS_API_KEY
  - ELEVENLABS_VOICE_ID exported OR passed via --voice-id
  - Whisper model (`uv run python scripts/fetch-models.py`)
  - macOS Accessibility permission for push-to-talk (spacebar mode)

This is NOT the real daemon — it's flat glue around the provider classes
so you can hear the full stack work before Unit 7 formalises the state
machine.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

DEFAULT_MODEL = Path.home() / ".herbert" / "models" / "ggml-base.en-q5_1.bin"
DEFAULT_PERSONA = (
    "You are Herbert, a retro-futurist home companion. Reply in one or two "
    "short sentences — friendly, a little dry, not a lecture."
)


def _read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16-bit mono PCM")
        return f.readframes(f.getnframes()), f.getframerate()


async def _capture_ptt(device_name: str | None) -> tuple[bytes, int]:
    """Block until spacebar pressed, capture until released."""
    from herbert.hal import PressEnded, PressStarted
    from herbert.hal.mac import MacEventSource, SounddeviceAudioIn

    mic = SounddeviceAudioIn(device_name=device_name)
    source = MacEventSource()
    print("[ptt] hold SPACEBAR to speak, release to send. (needs Accessibility perm)")
    events = source.events()
    try:
        async for evt in events:
            if isinstance(evt, PressStarted):
                break

        stop = asyncio.Event()

        async def _wait_release(event_iter: asyncio.AsyncIterator[object], stop_event: asyncio.Event) -> None:
            async for e in event_iter:
                if isinstance(e, PressEnded):
                    stop_event.set()
                    return

        _, pcm = await asyncio.gather(
            _wait_release(events, stop),
            mic.capture_until_released(stop),
        )
        return pcm, mic.sample_rate
    finally:
        await source.close()


async def _capture_fixed(seconds: float, device_name: str | None) -> tuple[bytes, int]:
    from herbert.hal.mac import SounddeviceAudioIn

    mic = SounddeviceAudioIn(device_name=device_name)
    stop = asyncio.Event()
    print(f"[rec] recording for {seconds:.1f}s...")

    async def _release_after() -> None:
        await asyncio.sleep(seconds)
        stop.set()

    _, pcm = await asyncio.gather(_release_after(), mic.capture_until_released(stop))
    return pcm, mic.sample_rate


async def _transcribe(pcm: bytes, sample_rate: int, model_path: Path) -> str:
    from herbert.stt.whisper_cpp import WhisperCppProvider

    if not model_path.exists():
        raise SystemExit(
            f"Whisper model not found at {model_path}\n"
            "  uv run python scripts/fetch-models.py"
        )
    provider = WhisperCppProvider(model_path)
    t0 = time.perf_counter()
    result = await provider.transcribe(pcm, sample_rate=sample_rate)
    wall = int((time.perf_counter() - t0) * 1000)
    print(f"[stt] ({wall}ms) {result.text!r}")
    return result.text


async def _speak(
    transcript: str,
    persona: str,
    anthropic_key: str,
    eleven_key: str,
    voice_id: str,
    model: str,
    output_device: str | None,
) -> None:
    from anthropic import AsyncAnthropic

    from herbert.hal.mac import SounddeviceAudioOut
    from herbert.llm.claude import LlmTurnState, stream_turn
    from herbert.session import InMemorySession
    from herbert.tts import TtsState
    from herbert.tts.elevenlabs_stream import ElevenLabsProvider

    # Set the api key for the Anthropic client via env — the SDK reads it
    os.environ["ANTHROPIC_API_KEY"] = anthropic_key
    client = AsyncAnthropic()

    session = InMemorySession()
    llm_state = LlmTurnState()

    # Tap into the LLM sentence stream so we can both log and feed to TTS
    async def _sentence_tap():  # type: ignore[no-untyped-def]
        print("[llm] streaming response...")
        async for sentence in stream_turn(
            transcript,
            session,
            persona,
            client=client,
            model=model,
            state=llm_state,
        ):
            print(f"[llm] {sentence!r}")
            yield sentence
        if llm_state.ttft_ms is not None:
            print(
                f"[llm] ttft={llm_state.ttft_ms}ms "
                f"first_sentence={llm_state.first_sentence_ms}ms "
                f"total={llm_state.total_ms}ms"
            )

    tts = ElevenLabsProvider(api_key=eleven_key, voice_id=voice_id)
    tts_state = TtsState()
    audio_out = SounddeviceAudioOut(device_name=output_device)

    async def _pcm_with_log():  # type: ignore[no-untyped-def]
        t_first = None
        async for chunk in tts.stream(_sentence_tap(), state=tts_state):
            if t_first is None:
                t_first = time.perf_counter()
                print(f"[tts] first chunk in {tts_state.ttfb_ms}ms")
            yield chunk
        print(
            f"[tts] done: {tts_state.bytes_produced} bytes in {tts_state.chunks_produced} chunks "
            f"across {tts_state.sentences_consumed} sentences"
        )

    await audio_out.play(_pcm_with_log(), sample_rate=tts.sample_rate)


def _load_keys(cli_voice_id: str | None) -> tuple[str, str, str]:
    from herbert.secrets import MissingSecretError, load_secrets

    store = load_secrets(Path.home() / ".herbert" / "secrets.env")
    try:
        anthropic = store.require("ANTHROPIC_API_KEY")
        eleven = store.require("ELEVENLABS_API_KEY")
    except MissingSecretError as exc:
        raise SystemExit(str(exc)) from exc
    voice_id = cli_voice_id or store.get("ELEVENLABS_VOICE_ID") or os.environ.get("ELEVENLABS_VOICE_ID")
    if not voice_id:
        raise SystemExit(
            "ELEVENLABS_VOICE_ID not set. Either:\n"
            "  1) add ELEVENLABS_VOICE_ID=<id> to ~/.herbert/secrets.env\n"
            "  2) export ELEVENLABS_VOICE_ID=<id>\n"
            "  3) pass --voice-id <id>"
        )
    return anthropic, eleven, voice_id


async def _main_async(args: argparse.Namespace) -> None:
    anthropic, eleven, voice_id = _load_keys(args.voice_id)

    if args.text:
        transcript = args.text
        print(f"[text] {transcript!r}")
    elif args.file:
        pcm, sr = _read_wav(Path(args.file))
        transcript = await _transcribe(pcm, sr, Path(args.model))
    elif args.seconds:
        pcm, sr = await _capture_fixed(args.seconds, args.input_device)
        transcript = await _transcribe(pcm, sr, Path(args.model))
    else:
        pcm, sr = await _capture_ptt(args.input_device)
        transcript = await _transcribe(pcm, sr, Path(args.model))

    if not transcript.strip():
        print("[stt] (empty transcript; nothing to ask Claude — try again)")
        return

    await _speak(
        transcript,
        args.persona,
        anthropic_key=anthropic,
        eleven_key=eleven,
        voice_id=voice_id,
        model=args.model_llm,
        output_device=args.output_device,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--model", default=str(DEFAULT_MODEL), help=f"Whisper model path (default: {DEFAULT_MODEL})")
    p.add_argument("--model-llm", default="claude-haiku-4-5", help="Claude model id")
    p.add_argument("--persona", default=DEFAULT_PERSONA, help="System prompt")
    p.add_argument("--voice-id", default=None, help="ElevenLabs voice id (or set ELEVENLABS_VOICE_ID)")
    p.add_argument("--input-device", default=None, help="Input device name substring")
    p.add_argument("--output-device", default=None, help="Output device name substring")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--seconds", type=float, help="Record fixed seconds instead of push-to-talk")
    src.add_argument("--file", help="Transcribe a WAV file instead of recording")
    src.add_argument("--text", help="Skip STT entirely; send this text to Claude")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("\n[demo] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
