"""Config loader: TOML file → typed HerbertConfig with env-var precedence."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "herbert" / "config.toml"


@dataclass
class WebConfig:
    bind_host: str = "127.0.0.1"
    port: int = 8080
    expose: bool = False


@dataclass
class SttConfig:
    provider: str = "whisper_cpp"
    model: str = "base.en-q5_1"
    input_device_name: str | None = None


@dataclass
class TtsConfig:
    provider: str = "elevenlabs"
    voice_id: str | None = None
    fallback_provider: str = "piper"
    output_device_name: str | None = None


@dataclass
class LlmConfig:
    # Sonnet is noticeably better at multi-step tool use and less prone
    # to hallucinating when a tool returns unhelpful data. Haiku is
    # faster but we're already paying 2-6s per tool call, so the
    # marginal model latency costs less than a wrong answer.
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024
    # Anthropic server-side tools. Each adds latency when it fires (2-6s
    # for search/fetch, more for code execution) but transforms what
    # Herbert can answer. Billed per invocation; Claude self-limits.
    web_search_enabled: bool = True
    # Loads a specific URL and reads the page. Pairs with web_search:
    # search returns a link, fetch gets the live content.
    web_fetch_enabled: bool = True
    # Python sandbox for calculations, API calls, date/time math.
    code_execution_enabled: bool = True


@dataclass
class LoggingConfig:
    log_transcripts: bool = True
    level: str = "INFO"


@dataclass
class McpConfig:
    servers: list[dict[str, str]] = field(default_factory=list)


@dataclass
class HerbertConfig:
    persona_path: Path = field(default_factory=lambda: Path.home() / ".herbert" / "persona.md")
    secrets_path: Path = field(default_factory=lambda: Path.home() / ".herbert" / "secrets.env")
    log_path: Path = field(default_factory=lambda: Path.home() / ".herbert" / "herbert.log")
    web: WebConfig = field(default_factory=WebConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    mcp: McpConfig = field(default_factory=McpConfig)


_TOP_LEVEL_SECTIONS = {"web", "stt", "tts", "llm", "logging", "mcp"}
_TOP_LEVEL_SCALARS = {"persona_path", "secrets_path", "log_path"}


def load_config(
    cli_path: Path | None = None,
    default_path: Path = DEFAULT_CONFIG_PATH,
) -> HerbertConfig:
    """Load config with precedence: CLI flag → HERBERT_CONFIG env → default path → built-in defaults."""
    path = _resolve_config_path(cli_path, default_path)
    if path is None or not path.exists():
        return HerbertConfig()
    data = tomllib.loads(path.read_text())
    return _build_config(data)


def _resolve_config_path(cli_path: Path | None, default_path: Path) -> Path | None:
    if cli_path is not None:
        return cli_path
    env_path = os.environ.get("HERBERT_CONFIG")
    if env_path:
        return Path(env_path)
    return default_path


def _build_config(data: dict[str, Any]) -> HerbertConfig:
    _validate_keys(data)
    kwargs: dict[str, Any] = {}
    for key in _TOP_LEVEL_SCALARS:
        if key in data:
            kwargs[key] = Path(data[key])
    section_map = {
        "web": WebConfig,
        "stt": SttConfig,
        "tts": TtsConfig,
        "llm": LlmConfig,
        "logging": LoggingConfig,
        "mcp": McpConfig,
    }
    for section, cls in section_map.items():
        if section in data:
            kwargs[section] = _build_section(cls, data[section])
    return HerbertConfig(**kwargs)


def _build_section(cls: type, data: dict[str, Any]) -> Any:
    field_names = {f.name for f in fields(cls)}
    unknown = set(data) - field_names
    if unknown:
        raise ValueError(f"unknown keys in [{cls.__name__}]: {sorted(unknown)}")
    return cls(**data)


def _validate_keys(data: dict[str, Any]) -> None:
    allowed = _TOP_LEVEL_SECTIONS | _TOP_LEVEL_SCALARS
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown top-level config keys: {sorted(unknown)}")


def as_dict(cfg: HerbertConfig) -> dict[str, Any]:
    """Serialize config to dict (for logging / diagnostics; NOT for secrets)."""
    out: dict[str, Any] = {}
    for f in fields(cfg):
        value = getattr(cfg, f.name)
        if is_dataclass(value) and not isinstance(value, type):
            out[f.name] = asdict(value)
        elif isinstance(value, Path):
            out[f.name] = str(value)
        else:
            out[f.name] = value
    return out
