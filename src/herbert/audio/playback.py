"""Audio playback helpers.

Platform-specific playback implementations live in `herbert.hal.<platform>`;
this module exists as the scoped location for any future non-HAL playback
utilities (e.g. PCM-to-WAV capture for tests) and re-exports the stable
public names for callers that want `from herbert.audio.playback import ...`.
"""

from herbert.hal import AudioOut
from herbert.hal.mock import MockAudioOut

__all__ = ["AudioOut", "MockAudioOut"]
