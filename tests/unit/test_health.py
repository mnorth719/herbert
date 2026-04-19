"""Startup health-check behavior — each check individually + orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from herbert.config import HerbertConfig, SttConfig, TtsConfig
from herbert.health import (
    HealthCheck,
    _check_model_file,
    _check_persona,
    _check_piper_voice,
    run_startup_checks,
)
from herbert.secrets import SecretsStore


def _config(tmp_path: Path, tts_provider: str = "elevenlabs") -> HerbertConfig:
    return HerbertConfig(
        persona_path=tmp_path / "persona.md",
        secrets_path=tmp_path / "secrets.env",
        log_path=tmp_path / "herbert.log",
        stt=SttConfig(provider="whisper_cpp"),
        tts=TtsConfig(provider=tts_provider),
    )


class TestIndividualChecks:
    async def test_model_missing_fails_with_path_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Redirect Path.home so we don't touch the real ~/.herbert
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        result = await _check_model_file(_config(tmp_path))
        assert result.name == "stt_model"
        assert result.ok is False
        assert "fetch-models.py" in result.message

    async def test_piper_voice_skipped_when_not_selected(
        self, tmp_path: Path
    ) -> None:
        result = await _check_piper_voice(_config(tmp_path, tts_provider="elevenlabs"))
        assert result is None

    async def test_piper_voice_missing_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        result = await _check_piper_voice(_config(tmp_path, tts_provider="piper"))
        assert result is not None
        assert result.ok is False
        assert "missing" in result.message

    async def test_piper_voice_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        voices = tmp_path / ".herbert" / "voices"
        voices.mkdir(parents=True)
        (voices / "en_US-lessac-medium.onnx").write_bytes(b"fake")
        (voices / "en_US-lessac-medium.onnx.json").write_text("{}")
        result = await _check_piper_voice(_config(tmp_path, tts_provider="piper"))
        assert result is not None and result.ok is True

    async def test_persona_missing_is_ok_fallback(self, tmp_path: Path) -> None:
        result = await _check_persona(_config(tmp_path))
        assert result.ok is True
        assert "default" in result.message

    async def test_persona_empty_file_fails(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path)
        cfg.persona_path.write_text("   \n")
        result = await _check_persona(cfg)
        assert result.ok is False
        assert "empty" in result.message

    async def test_persona_present(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path)
        cfg.persona_path.write_text("you are herbert")
        result = await _check_persona(cfg)
        assert result.ok is True
        assert "bytes" in result.message


class TestRunStartupChecks:
    async def test_results_are_health_check_instances(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        store = SecretsStore(path=tmp_path / "secrets.env")
        checks = await run_startup_checks(
            _config(tmp_path, tts_provider="piper"),
            store,
            include_audio=False,
        )
        assert all(isinstance(c, HealthCheck) for c in checks)
        # Always run: stt_model, persona; + tts_voice (piper), anthropic, (elevenlabs skipped)
        names = {c.name for c in checks}
        assert "stt_model" in names
        assert "persona" in names
        assert "tts_voice" in names
        # Elevenlabs skipped since provider is piper
        assert "elevenlabs_reachable" not in names

    async def test_elevenlabs_check_runs_when_provider_selected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        store = SecretsStore(path=tmp_path / "secrets.env")
        # Force the HTTP check to short-circuit rather than hit the real API
        checks = await run_startup_checks(
            _config(tmp_path, tts_provider="elevenlabs"),
            store,
            include_audio=False,
        )
        names = {c.name for c in checks}
        assert "elevenlabs_reachable" in names
