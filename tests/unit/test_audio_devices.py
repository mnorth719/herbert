"""Device-pinning behavior: substring match, ambiguity, not-found diagnostics."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from herbert.audio import devices


def _fake_devices(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return entries


def test_resolve_input_device_substring_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [
                {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "Fifine K669 USB Mic", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2},
            ]
        ),
    )
    assert devices.resolve_input_device("fifine") == 1


def test_resolve_input_device_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [{"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0}]
        ),
    )
    assert devices.resolve_input_device("MICROPHONE") == 0


def test_resolve_input_device_not_found_lists_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [
                {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "Another Mic", "max_input_channels": 2, "max_output_channels": 0},
            ]
        ),
    )
    with pytest.raises(devices.DeviceNotFoundError) as excinfo:
        devices.resolve_input_device("fifine")
    msg = str(excinfo.value)
    # Error message names the requested substring AND lists every available input device
    assert "fifine" in msg
    assert "MacBook Pro Microphone" in msg
    assert "Another Mic" in msg


def test_resolve_input_device_skips_output_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [
                {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2},
                {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
            ]
        ),
    )
    # "macbook pro" matches both devices by name, but only the input device qualifies
    assert devices.resolve_input_device("macbook pro") == 1


def test_resolve_input_device_ambiguity_warns_and_picks_first(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [
                {"name": "USB Audio Device #1", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "USB Audio Device #2", "max_input_channels": 1, "max_output_channels": 0},
            ]
        ),
    )
    with caplog.at_level(logging.WARNING, logger="herbert.audio.devices"):
        assert devices.resolve_input_device("usb audio") == 0
    assert any("multiple" in rec.message.lower() for rec in caplog.records)


def test_resolve_output_device_selects_output_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [
                {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
                {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2},
            ]
        ),
    )
    assert devices.resolve_output_device("speakers") == 1


def test_resolve_output_device_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        devices,
        "_query_devices",
        lambda: _fake_devices(
            [{"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2}]
        ),
    )
    with pytest.raises(devices.DeviceNotFoundError):
        devices.resolve_output_device("fifine-speaker")
