"""Secrets loader tests: fail-closed, permissions, bearer token generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from herbert.secrets import (
    MissingSecretError,
    ensure_frontend_bearer_token,
    load_secrets,
)


def write_secrets(path: Path, body: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(mode)
    return path


class TestSecretsLoader:
    def test_loads_key_value_pairs(self, tmp_path: Path) -> None:
        path = write_secrets(
            tmp_path / "secrets.env",
            "ANTHROPIC_API_KEY=sk-ant-123\nELEVENLABS_API_KEY=sk_el_456\n",
        )
        store = load_secrets(path)
        assert store.get("ANTHROPIC_API_KEY") == "sk-ant-123"
        assert store.get("ELEVENLABS_API_KEY") == "sk_el_456"

    def test_strips_quotes(self, tmp_path: Path) -> None:
        path = write_secrets(tmp_path / "secrets.env", 'KEY="quoted value"\n')
        store = load_secrets(path)
        assert store.get("KEY") == "quoted value"

    def test_ignores_comments_and_blank_lines(self, tmp_path: Path) -> None:
        path = write_secrets(
            tmp_path / "secrets.env",
            "# a comment\n\nANTHROPIC_API_KEY=sk-ant-123\n# trailing\n",
        )
        store = load_secrets(path)
        assert store.get("ANTHROPIC_API_KEY") == "sk-ant-123"


class TestFailClosed:
    def test_require_missing_key_raises(self, tmp_path: Path) -> None:
        path = write_secrets(tmp_path / "secrets.env", "OTHER=xyz\n")
        store = load_secrets(path)
        with pytest.raises(MissingSecretError, match="ANTHROPIC_API_KEY"):
            store.require("ANTHROPIC_API_KEY")

    def test_missing_secrets_file_is_empty_store(self, tmp_path: Path) -> None:
        store = load_secrets(tmp_path / "nonexistent.env")
        assert store.get("ANTHROPIC_API_KEY") is None
        with pytest.raises(MissingSecretError):
            store.require("ANTHROPIC_API_KEY")


class TestPermissions:
    def test_0600_passes_silently(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        path = write_secrets(tmp_path / "secrets.env", "K=v\n", mode=0o600)
        load_secrets(path)
        assert "permission" not in caplog.text.lower()

    def test_0644_warns_but_continues(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        caplog.set_level(logging.WARNING)
        path = write_secrets(tmp_path / "secrets.env", "K=v\n", mode=0o644)
        store = load_secrets(path)
        assert store.get("K") == "v"
        assert any("permission" in r.message.lower() for r in caplog.records)


class TestBearerTokenGeneration:
    def test_generates_token_when_missing(self, tmp_path: Path) -> None:
        path = write_secrets(tmp_path / "secrets.env", "ANTHROPIC_API_KEY=sk-ant-1\n")
        token = ensure_frontend_bearer_token(path)
        assert token
        assert len(token) >= 32

        # Token persisted to file for next run
        store = load_secrets(path)
        assert store.get("FRONTEND_BEARER_TOKEN") == token

    def test_reuses_existing_token(self, tmp_path: Path) -> None:
        path = write_secrets(
            tmp_path / "secrets.env",
            "ANTHROPIC_API_KEY=sk-ant-1\nFRONTEND_BEARER_TOKEN=already-exists-123\n",
        )
        token = ensure_frontend_bearer_token(path)
        assert token == "already-exists-123"

    def test_generated_token_file_is_0600(self, tmp_path: Path) -> None:
        path = write_secrets(tmp_path / "secrets.env", "ANTHROPIC_API_KEY=sk-ant-1\n", mode=0o644)
        ensure_frontend_bearer_token(path)
        mode = path.stat().st_mode & 0o777
        # After write we normalize to 0600
        assert mode == 0o600

    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "new_secrets.env"
        token = ensure_frontend_bearer_token(path)
        assert token
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o600
