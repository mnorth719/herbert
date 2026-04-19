"""Build Anthropic `mcp_servers` kwargs from config.

v1 returns `[]` — no MCPs are enabled. The function exists so the call site
in `claude.stream_turn` is already shaped correctly; enabling a server in
v2 becomes a config edit rather than a code change.

Beta header note: the current shape uses `anthropic-beta: mcp-client-2025-11-20`.
This was current as of planning (2026-04-18); reconfirm at v2 implementation
time before relying on it. See plan Key Technical Decisions for context.
"""

from __future__ import annotations

from herbert.config import McpConfig

MCP_BETA_HEADER = "mcp-client-2025-11-20"


def build_mcp_servers(mcp: McpConfig) -> list[dict[str, str]]:
    """Validate each entry and return the list shape Anthropic's beta expects.

    Each entry in `mcp.servers` must at minimum have a `name` and `url`.
    Missing fields raise `ValueError` at load time — a misconfigured MCP
    should fail the startup check, not the first live turn.
    """
    servers: list[dict[str, str]] = []
    for idx, entry in enumerate(mcp.servers):
        if "name" not in entry or "url" not in entry:
            raise ValueError(
                f"mcp.servers[{idx}] missing required keys (need 'name' and 'url'): {entry}"
            )
        server: dict[str, str] = {
            "type": "url",
            "url": entry["url"],
            "name": entry["name"],
        }
        if "authorization_token" in entry:
            server["authorization_token"] = entry["authorization_token"]
        servers.append(server)
    return servers
