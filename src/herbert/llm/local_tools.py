"""Client-side tools — executed locally, not by Anthropic.

Unlike the server-side tools (web_search, web_fetch, code_execution) that
Anthropic runs transparently, client-side tools come back as
`stop_reason='tool_use'` and expect us to execute them and return a
`tool_result` block before Claude continues. The streaming loop in
`claude.stream_turn` handles that protocol; this module provides:

  - tool specs Claude sees in its `tools` list
  - `LocalToolDispatcher.execute(name, input)` which runs the right
    handler and returns a short string summary used as the tool_result

Design rules:
  - Tools are narrow. Each one does exactly one thing, named after its
    effect, with a minimal input schema.
  - Handlers publish events on the bus rather than mutating state directly;
    the daemon's usual event consumers do the actual work.
  - Results are short strings — Claude uses them to continue, not to
    render; extra prose would just waste tokens.
"""

from __future__ import annotations

import logging
from typing import Any

from herbert.events import AsyncEventBus, ViewChanged

log = logging.getLogger(__name__)


# --- Tool specs ----------------------------------------------------------

SET_VIEW_TOOL: dict[str, Any] = {
    "name": "set_view",
    "description": (
        "Switch the frontend display between 'character' (Herbert's face, the default) "
        "and 'diagnostic' (a scrolling log tail). Call this when Matt asks to see the "
        "logs, enter diagnostic mode, debug mode, show the innards, or any phrasing "
        "that reads as 'show me what's going on internally' — use your judgment for "
        "intent; Matt's speech is subject to transcription errors so exact wording "
        "varies. Do NOT call this for factual questions like 'show me the logs from "
        "yesterday' (that's asking about log content, not switching the UI view). "
        "After the tool returns, a brief acknowledgement is fine but not required — "
        "Matt will see the view change."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["character", "diagnostic"],
                "description": (
                    "The target view: 'character' shows Herbert's face, "
                    "'diagnostic' shows the log tail."
                ),
            },
        },
        "required": ["mode"],
    },
}


# Ordered so the list in tools.py stays deterministic.
ALL_LOCAL_TOOLS: list[dict[str, Any]] = [SET_VIEW_TOOL]

# Quick lookup set for the stream loop — "is this tool one WE need to run?"
LOCAL_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in ALL_LOCAL_TOOLS)


# --- Dispatcher ----------------------------------------------------------


class LocalToolDispatcher:
    """Maps tool_use block names to handlers. One instance per daemon."""

    def __init__(self, bus: AsyncEventBus) -> None:
        self._bus = bus

    async def execute(self, name: str, tool_input: dict[str, Any], turn_id: str | None) -> str:
        """Run the named tool, return a short string for the tool_result."""
        if name == "set_view":
            return await self._set_view(tool_input, turn_id)
        log.warning("local tool %r not recognised; Claude may have hallucinated it", name)
        return f"error: tool {name!r} is not available"

    async def _set_view(self, tool_input: dict[str, Any], turn_id: str | None) -> str:
        mode = tool_input.get("mode")
        if mode not in ("character", "diagnostic"):
            return f"error: mode must be 'character' or 'diagnostic', got {mode!r}"
        await self._bus.publish(ViewChanged(turn_id=turn_id, view=mode))
        log.info("local tool set_view → %s", mode)
        return f"view set to {mode}"
