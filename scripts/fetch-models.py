#!/usr/bin/env python3
"""Download Herbert's model + voice dependencies with SHA256 verification.

Two artifact kinds, two destination directories:

  models  -> ~/.herbert/models/   (whisper.cpp .bin)
  voices  -> ~/.herbert/voices/   (Piper .onnx + .onnx.json sidecar)

Run once per machine as part of first-boot setup. Re-runs are idempotent:
already-present files with matching SHA256 are skipped.

  # Grab everything (default — safe re-runnable shape)
  uv run python scripts/fetch-models.py --all

  # Just the STT model
  uv run python scripts/fetch-models.py --model base.en-q5_1

  # Just the Piper fallback voice
  uv run python scripts/fetch-models.py --voice en_US-lessac-medium

Pins: each spec carries a SHA256 we verify against. For fresh downloads
whose hash isn't pinned yet, pass --trust-on-first-use; the script prints
the observed SHA256 so you can pin it in the MODELS / VOICES dicts below.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DEST = Path.home() / ".herbert" / "models"
VOICES_DEST = Path.home() / ".herbert" / "voices"

MODELS: dict[str, dict[str, str | None]] = {
    "base.en-q5_1": {
        "filename": "ggml-base.en-q5_1.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en-q5_1.bin",
        "sha256": "4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f",
    },
}

# Piper voices are shipped as two files (the model + its JSON sidecar).
# We fetch both per voice; the sidecar is small and checksummed too.
VOICES: dict[str, list[dict[str, str | None]]] = {
    "en_US-lessac-medium": [
        {
            "filename": "en_US-lessac-medium.onnx",
            "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
            "sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        },
        {
            "filename": "en_US-lessac-medium.onnx.json",
            "url": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
            "sha256": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
        },
    ],
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_spec(
    spec: dict[str, str | None],
    dest_dir: Path,
    *,
    trust_on_first_use: bool,
    pin_key: str,
) -> Path:
    """Download one {filename, url, sha256} entry into `dest_dir`."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / spec["filename"]  # type: ignore[operator]
    pinned = spec["sha256"]

    if target.exists():
        observed = _sha256(target)
        if pinned and observed == pinned:
            print(f"{target.name}: already present and verified")
            return target
        if pinned and observed != pinned:
            print(f"{target.name}: SHA256 mismatch (have {observed}); re-downloading")
        else:
            print(f"{target.name}: already present (no pin — run with --trust-on-first-use to pin)")
            if trust_on_first_use:
                print(f"  observed SHA256: {observed}")
                _pin_hint(pin_key, target.name, observed)
            return target

    print(f"Fetching {spec['url']}")
    print(f"  -> {target}")
    urllib.request.urlretrieve(spec["url"], target)
    observed = _sha256(target)

    if pinned:
        if observed != pinned:
            target.unlink()
            raise SystemExit(
                f"SHA256 mismatch for {target.name}: expected {pinned}, got {observed}"
            )
        print(f"{target.name}: SHA256 verified")
    else:
        if not trust_on_first_use:
            target.unlink()
            raise SystemExit(
                f"{target.name}: no pinned SHA256 and --trust-on-first-use not set; refusing."
            )
        print(f"{target.name}: SHA256 observed (pin this): {observed}")
        _pin_hint(pin_key, target.name, observed)
    return target


def fetch_model(model: str, dest_dir: Path, *, trust_on_first_use: bool) -> Path:
    if model not in MODELS:
        raise SystemExit(f"unknown model {model!r}; known: {sorted(MODELS)}")
    return _fetch_spec(
        MODELS[model], dest_dir, trust_on_first_use=trust_on_first_use, pin_key=f"MODELS[{model!r}]"
    )


def fetch_voice(voice: str, dest_dir: Path, *, trust_on_first_use: bool) -> list[Path]:
    if voice not in VOICES:
        raise SystemExit(f"unknown voice {voice!r}; known: {sorted(VOICES)}")
    return [
        _fetch_spec(
            spec,
            dest_dir,
            trust_on_first_use=trust_on_first_use,
            pin_key=f"VOICES[{voice!r}][{i}]",
        )
        for i, spec in enumerate(VOICES[voice])
    ]


def _pin_hint(pin_key: str, filename: str, observed: str) -> None:
    print(
        f"\n  To pin: edit scripts/fetch-models.py and set\n"
        f"    {pin_key}['sha256'] = {observed!r}  # for {filename}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--model",
        choices=sorted(MODELS),
        help="Whisper model identifier to fetch.",
    )
    parser.add_argument(
        "--voice",
        choices=sorted(VOICES),
        help="Piper voice identifier to fetch.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch the default model + voice (base.en-q5_1 + en_US-lessac-medium).",
    )
    parser.add_argument(
        "--models-dest",
        type=Path,
        default=MODELS_DEST,
        help=f"Model destination directory (default: {MODELS_DEST}).",
    )
    parser.add_argument(
        "--voices-dest",
        type=Path,
        default=VOICES_DEST,
        help=f"Voice destination directory (default: {VOICES_DEST}).",
    )
    parser.add_argument(
        "--trust-on-first-use",
        action="store_true",
        help="Accept a freshly-downloaded file whose SHA256 is not yet pinned.",
    )
    args = parser.parse_args(argv)

    # If nothing specific requested, default to fetching the primary model
    # (preserves the earlier single-flag behaviour for existing users).
    if not any([args.model, args.voice, args.all]):
        args.model = "base.en-q5_1"

    if args.all:
        args.model = args.model or "base.en-q5_1"
        args.voice = args.voice or "en_US-lessac-medium"

    if args.model:
        fetch_model(args.model, args.models_dest, trust_on_first_use=args.trust_on_first_use)
    if args.voice:
        fetch_voice(args.voice, args.voices_dest, trust_on_first_use=args.trust_on_first_use)
    return 0


if __name__ == "__main__":
    sys.exit(main())
