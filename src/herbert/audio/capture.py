"""Audio capture helpers.

Platform-specific capture implementations live in `herbert.hal.<platform>`;
this module exists as the scoped location for any future non-HAL capture
utilities (e.g. offline PCM file replay) and re-exports the stable public
names for callers that want `from herbert.audio.capture import ...`.
"""

from herbert.hal import AudioIn
from herbert.hal.mock import MockAudioIn

__all__ = ["AudioIn", "MockAudioIn"]
