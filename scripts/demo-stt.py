#!/usr/bin/env python3
"""Smoke test for the HAL + STT stack: speak, see a transcript.

Three modes:

  # Push-to-talk (hold spacebar, speak, release):
  uv run scripts/demo-stt.py

  # Fixed-duration record (no pynput / no Accessibility needed):
  uv run scripts/demo-stt.py --seconds 5

  # Transcribe a pre-recorded 16kHz mono WAV:
  uv run scripts/demo-stt.py --file path/to/input.wav

Prereqs (first run only):
  uv run python scripts/fetch-models.py --trust-on-first-use

Notes:
  - Push-to-talk mode needs macOS Accessibility permission for the terminal
    running this script (System Settings → Privacy & Security → Accessibility).
    If the spacebar appears dead, that's almost certainly why.
  - Change the input device via --device "fifine" (substring match).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

DEFAULT_MODEL = Path.home() / ".herbert" / "models" / "ggml-base.en-q5_1.bin"


def _read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise SystemExit(
                f"{path}: expected 16-bit mono PCM, got "
                f"{f.getnchannels()}ch/{f.getsampwidth() * 8}bit"
            )
        return f.readframes(f.getnframes()), f.getframerate()


async def _transcribe(pcm: bytes, sample_rate: int, model_path: Path) -> None:
    from herbert.stt.whisper_cpp import WhisperCppProvider

    if not model_path.exists():
        raise SystemExit(
            f"model not found at {model_path}\n"
            "  run: uv run python scripts/fetch-models.py --trust-on-first-use"
        )

    provider = WhisperCppProvider(model_path)
    print(f"[whisper] transcribing {len(pcm)} bytes @ {sample_rate}Hz...")
    t0 = time.perf_counter()
    result = await provider.transcribe(pcm, sample_rate=sample_rate)
    wall_ms = int((time.perf_counter() - t0) * 1000)
    print(f"[whisper] ({wall_ms}ms wall, {result.duration_ms}ms inference)")
    print(f"> {result.text!r}")


async def _run_file(args: argparse.Namespace) -> None:
    pcm, sr = _read_wav(Path(args.file))
    await _transcribe(pcm, sr, Path(args.model))


async def _run_fixed(args: argparse.Namespace) -> None:
    from herbert.hal.mac import SounddeviceAudioIn

    mic = SounddeviceAudioIn(device_name=args.device)
    stop = asyncio.Event()
    print(f"[rec] recording for {args.seconds:.1f}s...")

    async def _release_after() -> None:
        await asyncio.sleep(args.seconds)
        stop.set()

    _, pcm = await asyncio.gather(_release_after(), mic.capture_until_released(stop))
    print(f"[rec] captured {len(pcm)} bytes")
    await _transcribe(pcm, mic.sample_rate, Path(args.model))


async def _run_ptt(args: argparse.Namespace) -> None:
    from herbert.hal import PressEnded, PressStarted
    from herbert.hal.mac import MacEventSource, SounddeviceAudioIn

    mic = SounddeviceAudioIn(device_name=args.device)
    source = MacEventSource()
    print("[ptt] hold SPACEBAR to speak, release to transcribe. Ctrl-C to quit.")
    print("      (needs Terminal -> Accessibility permission on macOS)")

    events = source.events()
    try:
        while True:
            # Wait for a PressStarted
            while True:
                evt = await events.__anext__()
                if isinstance(evt, PressStarted):
                    break

            # Spawn release-waiter and capture in parallel
            stop = asyncio.Event()

            async def _wait_release(event_iter, stop_event) -> None:
                async for e in event_iter:
                    if isinstance(e, PressEnded):
                        stop_event.set()
                        return

            t0 = time.perf_counter()
            _, pcm = await asyncio.gather(
                _wait_release(events, stop),
                mic.capture_until_released(stop),
            )
            rec_ms = int((time.perf_counter() - t0) * 1000)
            print(f"[rec] captured {len(pcm)} bytes in {rec_ms}ms")
            await _transcribe(pcm, mic.sample_rate, Path(args.model))
            print()
    finally:
        await source.close()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--model",
        default=str(DEFAULT_MODEL),
        help=f"Path to ggml-*.bin whisper model (default: {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--device",
        default=None,
        help="Input device name substring (e.g. 'fifine'). Default: system default.",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--seconds",
        type=float,
        help="Record for a fixed number of seconds instead of push-to-talk.",
    )
    g.add_argument(
        "--file",
        help="Transcribe a pre-recorded WAV (16kHz mono int16) instead of recording.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.file:
        asyncio.run(_run_file(args))
    elif args.seconds:
        asyncio.run(_run_fixed(args))
    else:
        try:
            asyncio.run(_run_ptt(args))
        except KeyboardInterrupt:
            print("\n[ptt] bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
