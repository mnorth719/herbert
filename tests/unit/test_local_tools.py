"""LocalToolDispatcher: dispatch + set_view handler."""

from __future__ import annotations

import asyncio

from herbert.events import AsyncEventBus, ViewChanged
from herbert.llm.local_tools import LOCAL_TOOL_NAMES, SET_VIEW_TOOL, LocalToolDispatcher


class TestSetViewHandler:
    async def test_diagnostic_mode_publishes_view_changed(self) -> None:
        bus = AsyncEventBus()
        dispatcher = LocalToolDispatcher(bus)

        async with bus.subscribe() as sub:
            result = await dispatcher.execute(
                "set_view", {"mode": "diagnostic"}, turn_id="t1"
            )
            event = await asyncio.wait_for(sub.receive(), timeout=1.0)

        assert isinstance(event, ViewChanged)
        assert event.view == "diagnostic"
        assert event.turn_id == "t1"
        assert "diagnostic" in result

    async def test_character_mode_publishes_view_changed(self) -> None:
        bus = AsyncEventBus()
        dispatcher = LocalToolDispatcher(bus)
        async with bus.subscribe() as sub:
            await dispatcher.execute("set_view", {"mode": "character"}, turn_id="t2")
            event = await asyncio.wait_for(sub.receive(), timeout=1.0)
        assert isinstance(event, ViewChanged)
        assert event.view == "character"

    async def test_invalid_mode_returns_error_and_does_not_publish(self) -> None:
        bus = AsyncEventBus()
        dispatcher = LocalToolDispatcher(bus)
        async with bus.subscribe() as sub:
            result = await dispatcher.execute(
                "set_view", {"mode": "yolo"}, turn_id=None
            )
            # No event should be published; subsequent receive would time out
            import contextlib

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(sub.receive(), timeout=0.1)
                raise AssertionError("unexpected ViewChanged event for invalid mode")
        assert "error" in result.lower()


class TestDispatch:
    async def test_unknown_tool_returns_error_string(self) -> None:
        dispatcher = LocalToolDispatcher(AsyncEventBus())
        result = await dispatcher.execute("nope_tool", {}, turn_id=None)
        assert "error" in result.lower()
        assert "nope_tool" in result


class TestSpec:
    def test_set_view_tool_has_required_shape(self) -> None:
        # Lightweight schema guard — the exact string is brittle but a few
        # structural invariants must hold for Anthropic to accept the tool.
        assert SET_VIEW_TOOL["name"] == "set_view"
        assert "description" in SET_VIEW_TOOL
        assert SET_VIEW_TOOL["input_schema"]["type"] == "object"
        assert "mode" in SET_VIEW_TOOL["input_schema"]["properties"]
        assert SET_VIEW_TOOL["input_schema"]["required"] == ["mode"]

    def test_local_tool_names_include_set_view(self) -> None:
        assert "set_view" in LOCAL_TOOL_NAMES
