"""MCP passthrough: validation + shape. v1 always runs with zero servers."""

from __future__ import annotations

import pytest

from herbert.config import McpConfig
from herbert.llm.mcp_passthrough import MCP_BETA_HEADER, build_mcp_servers


def test_empty_config_returns_empty_list() -> None:
    assert build_mcp_servers(McpConfig()) == []


def test_valid_entry_produces_url_shape() -> None:
    cfg = McpConfig(servers=[{"name": "demo", "url": "https://example.com/mcp"}])
    result = build_mcp_servers(cfg)
    assert result == [
        {"type": "url", "name": "demo", "url": "https://example.com/mcp"}
    ]


def test_entry_with_auth_token_preserved() -> None:
    cfg = McpConfig(
        servers=[
            {
                "name": "demo",
                "url": "https://example.com/mcp",
                "authorization_token": "tok-123",
            }
        ]
    )
    assert build_mcp_servers(cfg)[0]["authorization_token"] == "tok-123"


def test_missing_name_raises_with_index() -> None:
    cfg = McpConfig(servers=[{"url": "https://example.com/mcp"}])
    with pytest.raises(ValueError, match=r"mcp\.servers\[0\]"):
        build_mcp_servers(cfg)


def test_missing_url_raises() -> None:
    cfg = McpConfig(servers=[{"name": "demo"}])
    with pytest.raises(ValueError, match="url"):
        build_mcp_servers(cfg)


def test_beta_header_value_is_pinned() -> None:
    # Shape guard: if the beta header string changes we want the test to force a review.
    assert MCP_BETA_HEADER == "mcp-client-2025-11-20"
