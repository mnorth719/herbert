"""Redaction filter tests: scrubs secrets from log output."""

from __future__ import annotations

import logging

import pytest

from herbert.logging import RedactingFilter


@pytest.fixture
def filter_() -> RedactingFilter:
    return RedactingFilter()


def make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=None, exc_info=None
    )


class TestSecretPatternRedaction:
    def test_redacts_anthropic_key(self, filter_: RedactingFilter) -> None:
        record = make_record("loaded key sk-ant-api03-abc123xyz from secrets")
        filter_.filter(record)
        assert "sk-ant" not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()

    def test_redacts_elevenlabs_key(self, filter_: RedactingFilter) -> None:
        record = make_record("ElevenLabs auth sk_el_abc123def456 succeeded")
        filter_.filter(record)
        assert "sk_el_abc123def456" not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()

    def test_redacts_xi_key(self, filter_: RedactingFilter) -> None:
        record = make_record("XI key xi_abc123def received")
        filter_.filter(record)
        assert "xi_abc123def" not in record.getMessage()

    def test_redacts_bearer_token(self, filter_: RedactingFilter) -> None:
        record = make_record("GET /ws Authorization: Bearer abc123.tokenhere Status 200")
        filter_.filter(record)
        assert "abc123.tokenhere" not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()


class TestUrlQueryParamRedaction:
    @pytest.mark.parametrize("param", ["token", "key", "bearer"])
    def test_redacts_url_query_param(self, filter_: RedactingFilter, param: str) -> None:
        record = make_record(f"GET /ws?{param}=mysecret123 HTTP/1.1")
        filter_.filter(record)
        assert "mysecret123" not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()

    def test_redacts_second_query_param(self, filter_: RedactingFilter) -> None:
        record = make_record("GET /ws?foo=bar&token=secret&x=y")
        filter_.filter(record)
        assert "secret" not in record.getMessage()
        assert "foo=bar" in record.getMessage()

    def test_case_insensitive_param_name(self, filter_: RedactingFilter) -> None:
        record = make_record("GET /ws?TOKEN=secret HTTP/1.1")
        filter_.filter(record)
        assert "secret" not in record.getMessage()


class TestNonSecretsUntouched:
    def test_normal_message_unchanged(self, filter_: RedactingFilter) -> None:
        record = make_record("user said hello, herbert replied with weather info")
        filter_.filter(record)
        assert record.getMessage() == "user said hello, herbert replied with weather info"

    def test_short_hyphenated_words_not_redacted(self, filter_: RedactingFilter) -> None:
        record = make_record("see docs at sk-blog for reference")  # not a real key
        filter_.filter(record)
        # sk- alone without the required 20+ char suffix should NOT match our pattern
        assert record.getMessage() == "see docs at sk-blog for reference"


class TestStructuredFields:
    def test_redacts_dict_field_named_api_key(self, filter_: RedactingFilter) -> None:
        record = make_record("auth succeeded")
        record.api_key = "sk-ant-api03-secret"  # type: ignore[attr-defined]
        filter_.filter(record)
        assert record.api_key == "[REDACTED]"

    def test_redacts_dict_field_named_token(self, filter_: RedactingFilter) -> None:
        record = make_record("ws connected")
        record.token = "abc.secret.xyz"  # type: ignore[attr-defined]
        filter_.filter(record)
        assert record.token == "[REDACTED]"

    def test_redacts_nested_authorization(self, filter_: RedactingFilter) -> None:
        record = make_record("request")
        record.authorization = "Bearer abc123xyz"  # type: ignore[attr-defined]
        filter_.filter(record)
        assert record.authorization == "[REDACTED]"
