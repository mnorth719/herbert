"""Boot-snapshot builder + logger."""

from __future__ import annotations

import logging

import pytest

from herbert.boot_snapshot import build_snapshot, estimate_tokens, log_snapshot
from herbert.config import HerbertConfig, LlmConfig, SttConfig, TtsConfig


def _default_config() -> HerbertConfig:
    return HerbertConfig(
        stt=SttConfig(provider="whisper_cpp", model="base.en-q5_1"),
        tts=TtsConfig(provider="piper"),
        llm=LlmConfig(
            model="claude-sonnet-4-6",
            web_search_enabled=True,
            web_fetch_enabled=True,
            code_execution_enabled=True,
        ),
    )


class TestBuildSnapshot:
    def test_shape_is_json_serialisable(self) -> None:
        import json

        snap = build_snapshot(
            config=_default_config(),
            platform="mac",
            mode="mac_hybrid",
            persona_text="You are Herbert.",
            tools=[{"name": "set_view"}, {"name": "web_search"}],
            mcp_servers=None,
            beta_headers=["code-execution-2025-05-22"],
        )
        # Must round-trip through JSON
        json.dumps(snap)

    def test_contains_expected_fields(self) -> None:
        snap = build_snapshot(
            config=_default_config(),
            platform="mac",
            mode="mac_hybrid",
            persona_text="You are Herbert.",
            tools=[{"name": "set_view"}],
            mcp_servers=None,
            beta_headers=None,
        )
        assert "version" in snap
        assert snap["platform"] == "mac"
        assert snap["mode"] == "mac_hybrid"
        assert snap["config"]["llm"]["model"] == "claude-sonnet-4-6"
        assert snap["tools"] == ["set_view"]
        assert snap["system_prompt"]["text"] == "You are Herbert."
        assert snap["system_prompt"]["char_count"] == len("You are Herbert.")
        assert snap["system_prompt"]["token_estimate"] > 0


class TestLogSnapshot:
    def test_dumps_system_prompt_to_log(self, caplog: pytest.LogCaptureFixture) -> None:
        snap = build_snapshot(
            config=_default_config(),
            platform="mac",
            mode="mac_hybrid",
            persona_text="You are Herbert.\nBe brief.",
            tools=[{"name": "set_view"}, {"name": "web_search"}],
            mcp_servers=None,
            beta_headers=["code-execution-2025-05-22"],
        )
        with caplog.at_level(logging.INFO, logger="herbert.boot_snapshot"):
            log_snapshot(snap)
        joined = "\n".join(rec.message for rec in caplog.records)
        assert "You are Herbert." in joined
        assert "Be brief." in joined
        assert "BEGIN SYSTEM PROMPT" in joined
        assert "END SYSTEM PROMPT" in joined

    def test_lists_tool_names(self, caplog: pytest.LogCaptureFixture) -> None:
        snap = build_snapshot(
            config=_default_config(),
            platform="mac",
            mode="mac_hybrid",
            persona_text="persona",
            tools=[{"name": "set_view"}, {"name": "web_search"}],
            mcp_servers=None,
            beta_headers=None,
        )
        with caplog.at_level(logging.INFO, logger="herbert.boot_snapshot"):
            log_snapshot(snap)
        joined = "\n".join(rec.message for rec in caplog.records)
        assert "set_view" in joined
        assert "web_search" in joined

    def test_reports_config_knobs(self, caplog: pytest.LogCaptureFixture) -> None:
        snap = build_snapshot(
            config=_default_config(),
            platform="mac",
            mode="mac_hybrid",
            persona_text="persona",
            tools=[],
            mcp_servers=None,
            beta_headers=None,
        )
        with caplog.at_level(logging.INFO, logger="herbert.boot_snapshot"):
            log_snapshot(snap)
        joined = "\n".join(rec.message for rec in caplog.records)
        assert "claude-sonnet-4-6" in joined
        assert "whisper_cpp" in joined
        assert "piper" in joined
        assert "web_search=True" in joined


class TestTokenEstimate:
    def test_short_text(self) -> None:
        assert estimate_tokens("a" * 20) == 5

    def test_empty_returns_at_least_one(self) -> None:
        assert estimate_tokens("") == 1
