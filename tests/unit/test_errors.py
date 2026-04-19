"""Error classification tests."""

from __future__ import annotations

from herbert.errors import classify_error, is_retryable


class TestClassifyByMessage:
    def test_auth_phrase(self) -> None:
        assert classify_error(Exception("authentication failed")) == "api_auth"

    def test_invalid_api_key(self) -> None:
        assert classify_error(ValueError("invalid_api_key")) == "api_auth"

    def test_rate_limit(self) -> None:
        assert classify_error(RuntimeError("rate_limit exceeded")) == "api_rate_limit"

    def test_429_shape(self) -> None:
        assert classify_error(RuntimeError("Got 429 from upstream")) == "api_rate_limit"

    def test_overloaded_529(self) -> None:
        assert classify_error(RuntimeError("529 overloaded_error")) == "network_transient"

    def test_generic_timeout(self) -> None:
        assert classify_error(TimeoutError("connection timeout")) == "wifi_down"

    def test_connection_refused(self) -> None:
        assert classify_error(ConnectionError("getaddrinfo failed")) == "wifi_down"

    def test_unknown(self) -> None:
        assert classify_error(KeyError("nothing obvious")) == "unknown"


class TestClassifyByTypedSdk:
    def test_eleven_labs_error(self) -> None:
        from herbert.tts.elevenlabs_stream import ElevenLabsError

        assert classify_error(ElevenLabsError("voice_not_found")) == "tts_error"

    def test_whisper_missing_model(self) -> None:
        from herbert.stt.whisper_cpp import WhisperModelMissingError

        assert classify_error(WhisperModelMissingError("nope")) == "whisper_error"


class TestRetryability:
    def test_network_transient_is_retryable(self) -> None:
        assert is_retryable("network_transient")
        assert is_retryable("wifi_down")

    def test_auth_is_not_retryable(self) -> None:
        assert not is_retryable("api_auth")
        assert not is_retryable("api_rate_limit")
        assert not is_retryable("tts_error")
