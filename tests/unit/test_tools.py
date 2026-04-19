"""Tool-spec builder + persona addendum."""

from __future__ import annotations

from herbert.llm.tools import WEB_SEARCH_PERSONA_ADDENDUM, WEB_SEARCH_TOOL, build_tools


def test_build_tools_disabled_returns_empty() -> None:
    assert build_tools(web_search_enabled=False) == []


def test_build_tools_enabled_includes_web_search() -> None:
    tools = build_tools(web_search_enabled=True)
    assert tools == [WEB_SEARCH_TOOL]
    assert tools[0]["type"] == "web_search_20250305"
    assert tools[0]["name"] == "web_search"


def test_persona_addendum_forbids_claude_owned_acknowledgement() -> None:
    # Regression guard: the local filler in claude.py is the canonical
    # covering phrase. If the addendum ever tells Claude to add its own
    # again we get duplicates ("let me check. just a moment. <answer>").
    addendum = WEB_SEARCH_PERSONA_ADDENDUM.lower()
    assert "do not" in addendum or "don't" in addendum
    assert "covering sentence" in addendum or "filler" in addendum
    assert "before calling web_search" in addendum or "before calling the tool" in addendum


def test_persona_addendum_forbids_urls_and_citations() -> None:
    addendum = WEB_SEARCH_PERSONA_ADDENDUM.lower()
    assert "url" in addendum
    assert "citation" in addendum or "bracket" in addendum
