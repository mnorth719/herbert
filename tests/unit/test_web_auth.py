"""Bearer-token verification behavior."""

from __future__ import annotations

from herbert.web.auth import AuthConfig, _extract_bearer


class TestExtractBearer:
    def test_bearer_header(self) -> None:
        assert _extract_bearer("Bearer abc123") == "abc123"

    def test_bearer_case_insensitive_scheme(self) -> None:
        assert _extract_bearer("bearer abc123") == "abc123"
        assert _extract_bearer("BEARER abc123") == "abc123"

    def test_missing_scheme(self) -> None:
        assert _extract_bearer("abc123") is None

    def test_wrong_scheme(self) -> None:
        assert _extract_bearer("Basic abc123") is None

    def test_empty_token(self) -> None:
        assert _extract_bearer("Bearer   ") is None

    def test_none_header(self) -> None:
        assert _extract_bearer(None) is None


class TestAuthConfigVerify:
    def test_unexposed_always_accepts(self) -> None:
        auth = AuthConfig(expose=False, bearer_token="secret")
        assert auth.verify(None) is True
        assert auth.verify("anything") is True

    def test_exposed_accepts_correct_token(self) -> None:
        auth = AuthConfig(expose=True, bearer_token="secret")
        assert auth.verify("secret") is True

    def test_exposed_rejects_incorrect_token(self) -> None:
        auth = AuthConfig(expose=True, bearer_token="secret")
        assert auth.verify("wrong") is False
        assert auth.verify(None) is False
        assert auth.verify("") is False

    def test_exposed_without_configured_token_rejects(self) -> None:
        auth = AuthConfig(expose=True, bearer_token=None)
        assert auth.verify("anything") is False
