"""Name-substring device pinning over sounddevice's device list.

We resolve a user-configured device-name substring to a concrete sounddevice
device index or name string at stream-open time. Substring matching (rather
than exact) survives USB re-enumeration renaming quirks; case is ignored.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class DeviceNotFoundError(RuntimeError):
    """Raised when a configured device-name substring matches zero devices."""


def _query_devices() -> list[dict[str, Any]]:
    import sounddevice as sd

    return list(sd.query_devices())


def _list_device_names(devices: list[dict[str, Any]], kind: str) -> list[str]:
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    return [d["name"] for d in devices if d.get(channel_key, 0) > 0]


def _resolve(name: str, kind: str) -> int:
    """Return the sounddevice index of the first device whose name contains `name`.

    `kind` is "input" or "output". Raises `DeviceNotFoundError` if no device
    matches; logs WARN and uses the first match if multiple devices match.
    """
    devices = _query_devices()
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    needle = name.lower()
    matches: list[tuple[int, dict[str, Any]]] = [
        (i, d)
        for i, d in enumerate(devices)
        if needle in d["name"].lower() and d.get(channel_key, 0) > 0
    ]
    if not matches:
        available = _list_device_names(devices, kind)
        raise DeviceNotFoundError(
            f"{kind} device matching {name!r} not found. Available {kind} devices: {available}"
        )
    if len(matches) > 1:
        names = [d["name"] for _, d in matches]
        log.warning(
            "multiple %s devices match %r (%s); using first: %r",
            kind,
            name,
            names,
            matches[0][1]["name"],
        )
    return matches[0][0]


def resolve_input_device(name: str) -> int:
    return _resolve(name, "input")


def resolve_output_device(name: str) -> int:
    return _resolve(name, "output")
