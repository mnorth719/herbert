"""Error classification for pipeline failures.

Maps raw provider / network exceptions onto the `ErrorClass` taxonomy the
state machine uses to pick a recovery policy (see `events.ErrorClass` and
plan R16). The classifier is conservative: unknown errors fall through to
`"unknown"` rather than being silently miscategorised into a retry path.
"""

from __future__ import annotations

from herbert.events import ErrorClass

# Error names we classify by string-match rather than importing, to keep
# this module free of heavy SDK imports and to survive SDK reshuffles.
_AUTH_MARKERS = ("authentication", "unauthorized", "invalid_api_key", "forbidden")
_RATE_LIMIT_MARKERS = ("rate_limit", "rate limit", "429", "quota")
_POLICY_MARKERS = ("policy", "content_policy", "policy_violation")
_CONNECTION_MARKERS = ("connection", "timeout", "network", "getaddrinfo", "nameresolutionerror")
_OVERLOADED_MARKERS = ("529", "overloaded", "server_error", "503", "502", "500", "504")


def classify_error(exc: BaseException) -> ErrorClass:
    """Map an arbitrary exception to one of the `ErrorClass` labels.

    Ordering matters: we check the most specific classes first. Anthropic's
    typed exceptions are caught by class (when present); everything else
    falls back to a string match on the class name and message.
    """
    classification = _classify_by_typed_sdk(exc)
    if classification is not None:
        return classification

    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    haystack = f"{name} {msg}"

    if _has_any(haystack, _AUTH_MARKERS):
        return "api_auth"
    if _has_any(haystack, _RATE_LIMIT_MARKERS):
        return "api_rate_limit"
    if _has_any(haystack, _POLICY_MARKERS):
        return "api_policy"
    if _has_any(haystack, _OVERLOADED_MARKERS):
        return "network_transient"
    if _has_any(haystack, _CONNECTION_MARKERS):
        return "wifi_down"
    return "unknown"


def _classify_by_typed_sdk(exc: BaseException) -> ErrorClass | None:
    """Catch the Anthropic + ElevenLabs typed exceptions we actually know about."""
    try:
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError):
            return "api_auth"
        if isinstance(exc, anthropic.PermissionDeniedError):
            return "api_auth"
        if isinstance(exc, anthropic.RateLimitError):
            return "api_rate_limit"
        if isinstance(exc, anthropic.BadRequestError):
            return "api_policy"
        if isinstance(exc, anthropic.APIConnectionError):
            return "wifi_down"
        if isinstance(exc, anthropic.InternalServerError):
            return "network_transient"
    except ImportError:
        pass

    # Our own TTS-level error is always a tts_error by construction
    try:
        from herbert.tts.elevenlabs_stream import ElevenLabsError

        if isinstance(exc, ElevenLabsError):
            return "tts_error"
    except ImportError:
        pass

    # Provider-file-missing errors are startup-shape failures
    from herbert.stt.whisper_cpp import WhisperModelMissingError
    from herbert.tts.piper import PiperVoiceMissingError

    if isinstance(exc, WhisperModelMissingError):
        return "whisper_error"
    if isinstance(exc, PiperVoiceMissingError):
        return "tts_error"

    return None


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def is_retryable(klass: ErrorClass) -> bool:
    """Whether a class should auto-retry (R16).

    Only transient network + overloaded conditions should loop on a timer;
    everything else is terminal until the user presses the button again.
    """
    return klass in ("network_transient", "wifi_down")
