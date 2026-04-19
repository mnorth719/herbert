"""Tool-spec builder + beta-header builder + persona addendum."""

from __future__ import annotations

from herbert.llm.tools import (
    CODE_EXECUTION_TOOL,
    TOOLS_PERSONA_ADDENDUM,
    WEB_FETCH_TOOL,
    WEB_SEARCH_PERSONA_ADDENDUM,
    WEB_SEARCH_TOOL,
    build_tool_beta_headers,
    build_tools,
)


class TestBuildTools:
    def test_all_disabled_returns_empty_when_locals_excluded(self) -> None:
        assert (
            build_tools(
                web_search_enabled=False,
                web_fetch_enabled=False,
                code_execution_enabled=False,
                include_local_tools=False,
            )
            == []
        )

    def test_locals_included_by_default(self) -> None:
        # Client-side tools come along unless explicitly excluded — set_view
        # etc. are considered part of Herbert's baseline capability.
        tools = build_tools(
            web_search_enabled=False,
            web_fetch_enabled=False,
            code_execution_enabled=False,
        )
        names = {t["name"] for t in tools}
        assert "set_view" in names

    def test_web_search_only_plus_locals(self) -> None:
        tools = build_tools(web_search_enabled=True, include_local_tools=False)
        assert tools == [WEB_SEARCH_TOOL]

    def test_all_enabled_in_order(self) -> None:
        tools = build_tools(
            web_search_enabled=True,
            web_fetch_enabled=True,
            code_execution_enabled=True,
            include_local_tools=False,
        )
        assert tools == [WEB_SEARCH_TOOL, WEB_FETCH_TOOL, CODE_EXECUTION_TOOL]

    def test_tool_ids_match_current_anthropic_versions(self) -> None:
        """Regression guard — if Anthropic rolls a tool forward, these pin
        the old IDs so the API error is localised to this test."""
        assert WEB_SEARCH_TOOL == {
            "type": "web_search_20250305",
            "name": "web_search",
        }
        assert WEB_FETCH_TOOL == {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
        }
        assert CODE_EXECUTION_TOOL == {
            "type": "code_execution_20250522",
            "name": "code_execution",
        }


class TestBetaHeaders:
    def test_no_tools_no_betas(self) -> None:
        assert build_tool_beta_headers() == []

    def test_web_fetch_requires_beta(self) -> None:
        betas = build_tool_beta_headers(web_fetch_enabled=True)
        assert betas == ["web-fetch-2025-09-10"]

    def test_code_execution_requires_beta(self) -> None:
        betas = build_tool_beta_headers(code_execution_enabled=True)
        assert betas == ["code-execution-2025-05-22"]

    def test_both_betas_collected(self) -> None:
        betas = build_tool_beta_headers(
            web_fetch_enabled=True, code_execution_enabled=True
        )
        assert "web-fetch-2025-09-10" in betas
        assert "code-execution-2025-05-22" in betas


class TestPersonaAddendum:
    def test_addendum_forbids_claude_owned_acknowledgement(self) -> None:
        # The local filler in claude.py is the canonical covering phrase.
        # If the addendum ever tells Claude to add its own filler we get
        # duplicates ("let me check. just a moment. <answer>").
        addendum = TOOLS_PERSONA_ADDENDUM.lower()
        assert "do not" in addendum or "don't" in addendum
        assert "covering sentence" in addendum or "filler" in addendum

    def test_addendum_names_all_three_tools(self) -> None:
        addendum = TOOLS_PERSONA_ADDENDUM.lower()
        assert "web_search" in addendum
        assert "web_fetch" in addendum
        assert "code_execution" in addendum

    def test_addendum_teaches_search_then_fetch_pattern(self) -> None:
        # The pattern that fixes stale-snippet failures — regression guard.
        addendum = TOOLS_PERSONA_ADDENDUM.lower()
        assert "search" in addendum and "fetch" in addendum
        assert "stale" in addendum or "cached" in addendum

    def test_backwards_compat_alias_still_exported(self) -> None:
        # Existing imports of WEB_SEARCH_PERSONA_ADDENDUM must keep working.
        assert WEB_SEARCH_PERSONA_ADDENDUM == TOOLS_PERSONA_ADDENDUM
