"""Config loader tests: precedence, defaults, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from herbert.config import HerbertConfig, as_dict, load_config


def write_toml(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


class TestConfigPrecedence:
    def test_cli_flag_wins_over_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_config = write_toml(tmp_path / "env.toml", 'persona_path = "/from/env.md"\n')
        cli_config = write_toml(tmp_path / "cli.toml", 'persona_path = "/from/cli.md"\n')
        monkeypatch.setenv("HERBERT_CONFIG", str(env_config))

        cfg = load_config(cli_path=cli_config)

        assert cfg.persona_path == Path("/from/cli.md")

    def test_env_wins_over_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_config = write_toml(tmp_path / "env.toml", 'persona_path = "/from/env.md"\n')
        monkeypatch.setenv("HERBERT_CONFIG", str(env_config))

        cfg = load_config(cli_path=None, default_path=tmp_path / "nonexistent.toml")

        assert cfg.persona_path == Path("/from/env.md")

    def test_default_used_when_no_cli_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        default_config = write_toml(tmp_path / "default.toml", 'persona_path = "/from/default.md"\n')
        monkeypatch.delenv("HERBERT_CONFIG", raising=False)

        cfg = load_config(cli_path=None, default_path=default_config)

        assert cfg.persona_path == Path("/from/default.md")

    def test_all_absent_returns_builtin_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HERBERT_CONFIG", raising=False)

        cfg = load_config(cli_path=None, default_path=tmp_path / "nonexistent.toml")

        assert isinstance(cfg, HerbertConfig)
        assert cfg.web.bind_host == "127.0.0.1"
        assert cfg.web.expose is False
        assert cfg.llm.model == "claude-sonnet-4-6"


class TestConfigDefaults:
    def test_web_defaults_to_localhost(self) -> None:
        cfg = HerbertConfig()
        assert cfg.web.bind_host == "127.0.0.1"
        assert cfg.web.expose is False
        assert 1 <= cfg.web.port <= 65535

    def test_stt_default_is_whisper_cpp(self) -> None:
        cfg = HerbertConfig()
        assert cfg.stt.provider == "whisper_cpp"

    def test_tts_default_is_elevenlabs(self) -> None:
        cfg = HerbertConfig()
        assert cfg.tts.provider == "elevenlabs"

    def test_log_transcripts_default_true(self) -> None:
        cfg = HerbertConfig()
        assert cfg.logging.log_transcripts is True


class TestConfigOverride:
    def test_toml_overrides_web_port(self, tmp_path: Path) -> None:
        path = write_toml(tmp_path / "h.toml", "[web]\nport = 9090\n")
        cfg = load_config(cli_path=path)
        assert cfg.web.port == 9090

    def test_toml_overrides_llm_model(self, tmp_path: Path) -> None:
        path = write_toml(tmp_path / "h.toml", '[llm]\nmodel = "claude-sonnet-4-6"\n')
        cfg = load_config(cli_path=path)
        assert cfg.llm.model == "claude-sonnet-4-6"

    def test_unknown_keys_rejected(self, tmp_path: Path) -> None:
        path = write_toml(tmp_path / "h.toml", "garbage_key = 42\n")
        with pytest.raises(ValueError, match="unknown"):
            load_config(cli_path=path)


class TestMemoryConfig:
    """MemoryConfig defaults, overrides, and serialization."""

    def test_memory_defaults(self) -> None:
        cfg = HerbertConfig()
        assert cfg.memory.enabled is True
        assert cfg.memory.inactivity_seconds == 300
        assert cfg.memory.recent_sessions_count == 5
        # Default path should end with memory.db under ~/.herbert
        assert cfg.memory.db_path.name == "memory.db"
        assert cfg.memory.db_path.parent.name == ".herbert"

    def test_toml_disables_memory(self, tmp_path: Path) -> None:
        path = write_toml(tmp_path / "h.toml", "[memory]\nenabled = false\n")
        cfg = load_config(cli_path=path)
        assert cfg.memory.enabled is False
        # Other defaults preserved
        assert cfg.memory.inactivity_seconds == 300
        assert cfg.memory.recent_sessions_count == 5

    def test_toml_overrides_inactivity_seconds(self, tmp_path: Path) -> None:
        path = write_toml(tmp_path / "h.toml", "[memory]\ninactivity_seconds = 600\n")
        cfg = load_config(cli_path=path)
        assert cfg.memory.inactivity_seconds == 600
        assert cfg.memory.enabled is True  # unspecified keys keep defaults

    def test_toml_overrides_db_path(self, tmp_path: Path) -> None:
        target = tmp_path / "custom" / "memory.db"
        path = write_toml(
            tmp_path / "h.toml",
            f'[memory]\ndb_path = "{target}"\n',
        )
        cfg = load_config(cli_path=path)
        assert cfg.memory.db_path == target

    def test_unknown_memory_key_rejected(self, tmp_path: Path) -> None:
        path = write_toml(tmp_path / "h.toml", "[memory]\nbogus = 1\n")
        with pytest.raises(ValueError, match="MemoryConfig"):
            load_config(cli_path=path)

    def test_as_dict_includes_memory(self) -> None:
        cfg = HerbertConfig()
        d = as_dict(cfg)
        assert "memory" in d
        assert d["memory"]["enabled"] is True
        assert d["memory"]["inactivity_seconds"] == 300
        assert d["memory"]["recent_sessions_count"] == 5
        # db_path rendered as string (Path serialisation contract already in use elsewhere)
        assert isinstance(d["memory"]["db_path"], str)
        assert d["memory"]["db_path"].endswith("memory.db")
