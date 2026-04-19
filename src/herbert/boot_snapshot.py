"""Boot-time + runtime observability — a structured snapshot of what
Herbert is currently configured to do, including the exact system prompt
Claude sees on every turn.

Two entry points:

  `build_snapshot(...) -> dict`  — pure data, JSON-serialisable. Served
      by `/api/boot_snapshot` so the diagnostic view can render it.
      Called fresh each request so persona hot-reload is reflected.

  `log_snapshot(...)`  — logs the same data in human-readable form to
      the file log at INFO, once at boot. Survives restarts in the log
      archive, greppable from the shell.

Matt's debugging flow: "Herbert said something weird" → open diagnostic
view → top-of-panel shows the current snapshot → compare against
expected → fix persona/config/tool state.

v2: extend with memory tier content (facts + recent session summaries)
once herbert.memory lands. The schema is stable enough that adding
sections won't require renaming fields.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from herbert import __version__
from herbert.config import HerbertConfig

log = logging.getLogger(__name__)

# Cheap heuristic. Anthropic's tokenizer differs, but for "at-a-glance"
# sanity checks this is within 15% for English prose. No tokenizer needed.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def build_snapshot(
    *,
    config: HerbertConfig,
    platform: str,
    mode: str,
    persona_text: str,
    tools: list[dict[str, Any]] | None,
    mcp_servers: list[dict[str, str]] | None,
    beta_headers: list[str] | None,
    stt_model_path: Path | None = None,
    tts_voice_path: Path | None = None,
    ready: bool | None = None,
) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of Herbert's current state.

    `persona_text` must be the *fully-assembled* system prompt — base
    persona + tools addendum + (future) memory sections — because that's
    what Claude actually sees on every turn.
    """
    return {
        "version": __version__,
        "platform": platform,
        "mode": mode,
        "ready": ready,
        "config": {
            "persona_path": str(config.persona_path),
            "secrets_path": str(config.secrets_path),
            "log_path": str(config.log_path),
            "web": {
                "bind_host": config.web.bind_host,
                "port": config.web.port,
                "expose": config.web.expose,
            },
            "stt": {
                "provider": config.stt.provider,
                "model": config.stt.model,
                "input_device": config.stt.input_device_name,
                "model_path": str(stt_model_path) if stt_model_path else None,
                "model_size_mb": _file_size_mb(stt_model_path),
            },
            "tts": {
                "provider": config.tts.provider,
                "voice_id": config.tts.voice_id,
                "fallback_provider": config.tts.fallback_provider,
                "output_device": config.tts.output_device_name,
                "voice_path": str(tts_voice_path) if tts_voice_path else None,
                "voice_size_mb": _file_size_mb(tts_voice_path),
            },
            "llm": {
                "model": config.llm.model,
                "max_tokens": config.llm.max_tokens,
                "web_search_enabled": config.llm.web_search_enabled,
                "web_fetch_enabled": config.llm.web_fetch_enabled,
                "code_execution_enabled": config.llm.code_execution_enabled,
            },
            "logging": {
                "level": config.logging.level,
                "log_transcripts": config.logging.log_transcripts,
            },
        },
        "tools": [t["name"] for t in (tools or [])],
        "mcp_servers_count": len(mcp_servers) if mcp_servers else 0,
        "beta_headers": list(beta_headers or []),
        "system_prompt": {
            "text": persona_text,
            "char_count": len(persona_text),
            "token_estimate": estimate_tokens(persona_text),
        },
    }


def log_snapshot(snapshot: dict[str, Any]) -> None:
    """Write the snapshot to the log in human-readable form at INFO."""
    log.info("=" * 68)
    log.info("Herbert %s boot snapshot", snapshot["version"])
    log.info("platform=%s mode=%s", snapshot["platform"], snapshot["mode"])
    log.info("")
    log.info("config:")
    cfg = snapshot["config"]
    log.info("  persona_path   = %s", cfg["persona_path"])
    log.info("  secrets_path   = %s", cfg["secrets_path"])
    log.info("  log_path       = %s", cfg["log_path"])
    log.info(
        "  web            = %s:%d (expose=%s)",
        cfg["web"]["bind_host"],
        cfg["web"]["port"],
        cfg["web"]["expose"],
    )
    stt = cfg["stt"]
    size = stt["model_size_mb"]
    log.info(
        "  stt            = %s model=%s input_device=%r",
        stt["provider"],
        stt["model"],
        stt["input_device"],
    )
    if stt["model_path"]:
        log.info(
            "  stt.model_path = %s%s",
            stt["model_path"],
            f" ({size:.1f} MB)" if size is not None else " (missing)",
        )
    tts = cfg["tts"]
    size = tts["voice_size_mb"]
    log.info(
        "  tts            = %s voice=%r fallback=%s output_device=%r",
        tts["provider"],
        tts["voice_id"],
        tts["fallback_provider"],
        tts["output_device"],
    )
    if tts["voice_path"]:
        log.info(
            "  tts.voice_path = %s%s",
            tts["voice_path"],
            f" ({size:.1f} MB)" if size is not None else " (missing)",
        )
    llm = cfg["llm"]
    log.info("  llm            = %s max_tokens=%d", llm["model"], llm["max_tokens"])
    log.info(
        "  llm.tools      = web_search=%s web_fetch=%s code_execution=%s",
        llm["web_search_enabled"],
        llm["web_fetch_enabled"],
        llm["code_execution_enabled"],
    )
    log.info(
        "  logging        = level=%s transcripts=%s",
        cfg["logging"]["level"],
        cfg["logging"]["log_transcripts"],
    )
    log.info("")
    log.info("active tools (%d): %s", len(snapshot["tools"]), snapshot["tools"] or "(none)")
    log.info("mcp_servers   : %d entries", snapshot["mcp_servers_count"])
    log.info("beta_headers  : %s", snapshot["beta_headers"] or "(none)")
    log.info("")
    prompt = snapshot["system_prompt"]
    log.info(
        "system prompt: %d chars, ~%d tokens",
        prompt["char_count"],
        prompt["token_estimate"],
    )
    log.info("--- BEGIN SYSTEM PROMPT ---")
    for line in prompt["text"].splitlines() or [""]:
        log.info("  %s", line)
    log.info("--- END SYSTEM PROMPT ---")
    log.info("=" * 68)


def _file_size_mb(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return None
