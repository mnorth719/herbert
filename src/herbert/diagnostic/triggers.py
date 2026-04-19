"""Whole-utterance regex matcher for diagnostic mode voice commands.

Design notes:
  - Whole-utterance match only. "Herbert show me the logs from yesterday"
    must NOT enter diagnostic mode — that's a normal factual question.
    We require the transcript, after normalisation, to equal one of the
    configured phrases (no prefix/suffix wrapping).
  - Normalisation: lowercase, strip leading/trailing whitespace, collapse
    internal whitespace to single spaces, strip trailing punctuation.
    This absorbs Whisper idiosyncrasies (extra spaces, trailing periods).
  - The trigger regex list is compiled once at import; `match()` is a
    pure function safe to call from the daemon's hot path.
"""

from __future__ import annotations

import re
from typing import Literal

Trigger = Literal["enter_diagnostic", "exit_diagnostic"]

# Each pattern is a raw phrase (no regex metacharacters). Whisper sometimes
# adds a leading "herbert" to match the wake-word framing, sometimes drops
# it. We accept both. Phrases are matched case-insensitive after
# normalisation; the compiled form is an anchored alternation.
_ENTER_PHRASES: tuple[str, ...] = (
    "herbert show me the logs",
    "show me the logs",
    "herbert diagnostic mode",
    "diagnostic mode",
    "herbert enter diagnostic",
    "enter diagnostic mode",
    "herbert show me the diagnostics",
    "show me diagnostics",
)

_EXIT_PHRASES: tuple[str, ...] = (
    "herbert exit diagnostic",
    "exit diagnostic",
    "exit diagnostic mode",
    "herbert character mode",
    "character mode",
    "herbert hide the logs",
    "hide the logs",
    "back to character",
    "herbert back to character",
)


def _compile(phrases: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(p) for p in phrases)
    # Anchored so partial matches ("show me the logs from yesterday") don't hit
    return re.compile(rf"^(?:{alts})$")


_ENTER_RE = _compile(_ENTER_PHRASES)
_EXIT_RE = _compile(_EXIT_PHRASES)

# Strip characters that Whisper + natural punctuation add but that shouldn't
# affect whole-utterance matching. Keep apostrophes for contractions even
# though we don't use any here — future phrases might ("herbert i'm done").
_TRAILING_PUNCT = re.compile(r"[.!?,;:]+$")
_WHITESPACE = re.compile(r"\s+")


def _normalise(transcript: str) -> str:
    s = transcript.strip().lower()
    s = _TRAILING_PUNCT.sub("", s)
    s = _WHITESPACE.sub(" ", s)
    return s.strip()


def match(transcript: str) -> Trigger | None:
    """Return `'enter_diagnostic'` / `'exit_diagnostic'` or `None`.

    The daemon calls this on every STT result BEFORE invoking the LLM.
    A hit short-circuits the turn — no LLM call, no session update,
    just a `ViewChanged` event and transition back to idle.
    """
    normalised = _normalise(transcript)
    if not normalised:
        return None
    if _ENTER_RE.match(normalised):
        return "enter_diagnostic"
    if _EXIT_RE.match(normalised):
        return "exit_diagnostic"
    return None
