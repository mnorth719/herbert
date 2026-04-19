"""Startup health checks — verify Herbert's prerequisites before the button matters.

Each check is small, independent, and carries its own timeout so a
degenerate external service can't stall boot. The daemon runs all checks
in parallel at startup, publishes an `ErrorOccurred` for each failure
(so the frontend boot sequence can surface them), and proceeds either
way — checks are diagnostic, not gate-keepers. Fail-closed startup lives
in the secrets layer, not here.

Checks:
  - model_file_present       ~/.herbert/models/<whisper>.bin exists
  - piper_voice_present      when tts.provider=piper, the voice + sidecar exist
  - mic_openable             try opening an InputStream for a tick (skipped on mock)
  - speaker_openable         same for OutputStream
  - anthropic_reachable      trivial API call with a short timeout
  - elevenlabs_reachable     HEAD to api.elevenlabs.io (only if selected)
  - persona_readable         file loads (or we fall back to the default — also ok)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from herbert.config import HerbertConfig
from herbert.secrets import SecretsStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthCheck:
    name: str
    ok: bool
    message: str
    duration_ms: int


# --- Individual checks ---------------------------------------------------


async def _check_model_file(config: HerbertConfig) -> HealthCheck:
    start = time.perf_counter()
    path = Path.home() / ".herbert" / "models" / "ggml-base.en-q5_1.bin"
    ok = path.exists()
    msg = str(path) if ok else f"missing: {path} (run scripts/fetch-models.py)"
    return HealthCheck("stt_model", ok, msg, _ms_since(start))


async def _check_piper_voice(config: HerbertConfig) -> HealthCheck | None:
    if config.tts.provider != "piper":
        return None
    start = time.perf_counter()
    voice = Path.home() / ".herbert" / "voices" / "en_US-lessac-medium.onnx"
    sidecar = voice.with_suffix(voice.suffix + ".json")
    missing = [p for p in (voice, sidecar) if not p.exists()]
    ok = not missing
    msg = (
        f"{voice.name} + sidecar present"
        if ok
        else f"missing: {', '.join(str(p) for p in missing)}"
    )
    return HealthCheck("tts_voice", ok, msg, _ms_since(start))


async def _check_audio_device(kind: str, device_name: str | None) -> HealthCheck:
    """Probe mic/speaker by briefly opening a stream. Skipped on mock HAL."""
    start = time.perf_counter()
    import sys

    if sys.platform not in ("darwin", "linux"):
        return HealthCheck(
            f"{kind}_open", True, f"skipped on {sys.platform}", _ms_since(start)
        )

    try:
        import sounddevice as sd  # type: ignore[import-untyped]

        # Resolve device if pinned, otherwise let PortAudio pick defaults.
        if device_name:
            from herbert.audio.devices import (
                resolve_input_device,
                resolve_output_device,
            )

            resolve = resolve_input_device if kind == "mic" else resolve_output_device
            device_index: int | None = resolve(device_name)
        else:
            device_index = None

        StreamCls = sd.RawInputStream if kind == "mic" else sd.RawOutputStream
        stream = StreamCls(
            samplerate=16000,
            channels=1,
            dtype="int16",
            device=device_index,
            blocksize=320,
        )
        stream.start()
        await asyncio.sleep(0.05)
        stream.stop()
        stream.close()
        return HealthCheck(f"{kind}_open", True, "ok", _ms_since(start))
    except Exception as exc:
        return HealthCheck(f"{kind}_open", False, str(exc), _ms_since(start))


async def _check_anthropic_reachable(
    config: HerbertConfig, secrets: SecretsStore
) -> HealthCheck:
    start = time.perf_counter()
    try:
        api_key = secrets.require("ANTHROPIC_API_KEY")
    except Exception as exc:
        return HealthCheck(
            "anthropic_reachable", False, f"missing key: {exc}", _ms_since(start)
        )
    try:
        import httpx

        # Models list is a cheap GET that authenticates + proves network
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            if 200 <= r.status_code < 500:
                # Any non-5xx (including 401 for dev keys) means the service
                # is reachable and responding — that's what we want to know.
                return HealthCheck(
                    "anthropic_reachable", True, f"HTTP {r.status_code}", _ms_since(start)
                )
            return HealthCheck(
                "anthropic_reachable",
                False,
                f"HTTP {r.status_code}",
                _ms_since(start),
            )
    except Exception as exc:
        return HealthCheck("anthropic_reachable", False, str(exc), _ms_since(start))


async def _check_elevenlabs_reachable(
    config: HerbertConfig, secrets: SecretsStore
) -> HealthCheck | None:
    if config.tts.provider != "elevenlabs":
        return None
    start = time.perf_counter()
    try:
        api_key = secrets.require("ELEVENLABS_API_KEY")
    except Exception as exc:
        return HealthCheck(
            "elevenlabs_reachable", False, f"missing key: {exc}", _ms_since(start)
        )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key},
            )
            if 200 <= r.status_code < 500:
                return HealthCheck(
                    "elevenlabs_reachable",
                    True,
                    f"HTTP {r.status_code}",
                    _ms_since(start),
                )
            return HealthCheck(
                "elevenlabs_reachable",
                False,
                f"HTTP {r.status_code}",
                _ms_since(start),
            )
    except Exception as exc:
        return HealthCheck("elevenlabs_reachable", False, str(exc), _ms_since(start))


async def _check_persona(config: HerbertConfig) -> HealthCheck:
    start = time.perf_counter()
    path = config.persona_path
    if not path.exists():
        return HealthCheck(
            "persona",
            True,
            f"{path} missing; using built-in default",
            _ms_since(start),
        )
    try:
        content = path.read_text()
    except OSError as exc:
        return HealthCheck("persona", False, f"unreadable: {exc}", _ms_since(start))
    if not content.strip():
        return HealthCheck("persona", False, f"empty: {path}", _ms_since(start))
    return HealthCheck(
        "persona", True, f"{path} ({len(content)} bytes)", _ms_since(start)
    )


# --- Top-level runner ---------------------------------------------------


async def run_startup_checks(
    config: HerbertConfig,
    secrets: SecretsStore,
    *,
    include_audio: bool = True,
) -> list[HealthCheck]:
    """Run every applicable check in parallel. Returns the results in order.

    `include_audio=False` is used by tests + mock-HAL runs so the sounddevice
    probes aren't attempted when no real devices exist.
    """
    tasks: list[asyncio.Task[HealthCheck | None]] = [
        asyncio.create_task(_check_model_file(config)),
        asyncio.create_task(_check_piper_voice(config)),
        asyncio.create_task(_check_anthropic_reachable(config, secrets)),
        asyncio.create_task(_check_elevenlabs_reachable(config, secrets)),
        asyncio.create_task(_check_persona(config)),
    ]
    if include_audio:
        tasks.extend(
            [
                asyncio.create_task(_check_audio_device("mic", config.stt.input_device_name)),
                asyncio.create_task(_check_audio_device("speaker", config.tts.output_device_name)),
            ]
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    checks: list[HealthCheck] = []
    for result in results:
        if result is None:
            continue
        if isinstance(result, BaseException):
            log.exception("health check crashed", exc_info=result)
            continue
        checks.append(result)
    return checks


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
