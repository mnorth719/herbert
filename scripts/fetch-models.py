#!/usr/bin/env python3
"""Download whisper.cpp (and later Piper) model files with SHA256 verification.

Run once per machine as part of first-boot setup. Re-runs are idempotent:
already-present files with matching SHA256 are skipped.

For models whose pinned SHA256 is not yet known (fresh checksum drift from
huggingface), pass `--trust-on-first-use`; the script prints the observed
SHA256 so it can be pinned in `MODELS` for future verifications.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

DEFAULT_DEST = Path.home() / ".herbert" / "models"

MODELS: dict[str, dict[str, str | None]] = {
    "base.en-q5_1": {
        "filename": "ggml-base.en-q5_1.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en-q5_1.bin",
        # Pin after first verified download via `--trust-on-first-use`.
        "sha256": None,
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(model: str, dest_dir: Path, *, trust_on_first_use: bool) -> Path:
    if model not in MODELS:
        raise SystemExit(f"unknown model {model!r}; known: {sorted(MODELS)}")
    spec = MODELS[model]
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
                _pin_hint(model, observed)
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
        _pin_hint(model, observed)
    return target


def _pin_hint(model: str, observed: str) -> None:
    print(
        f"\n  To pin: edit scripts/fetch-models.py and set\n"
        f"    MODELS[{model!r}]['sha256'] = {observed!r}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--model",
        default="base.en-q5_1",
        choices=sorted(MODELS),
        help="Model identifier to fetch (default: base.en-q5_1).",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Destination directory (default: {DEFAULT_DEST}).",
    )
    parser.add_argument(
        "--trust-on-first-use",
        action="store_true",
        help="Accept a freshly-downloaded model whose SHA256 is not yet pinned.",
    )
    args = parser.parse_args(argv)
    fetch(args.model, args.dest, trust_on_first_use=args.trust_on_first_use)
    return 0


if __name__ == "__main__":
    sys.exit(main())
