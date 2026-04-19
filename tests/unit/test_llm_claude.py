"""stream_turn: session threading + TTFT tracking + MCP header shape.

These tests inject a stub `messages.stream(...)` context manager — we never
talk to the real Anthropic SDK here. The live smoke test lives in
`tests/integration/test_llm_live.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from herbert.llm.claude import LlmTurnState, stream_turn
from herbert.session import InMemorySession, Message


class _TextDeltaEvent:
    """Mimics Anthropic's RawContentBlockDeltaEvent for a text_delta."""

    type = "content_block_delta"

    def __init__(self, text: str) -> None:
        self.delta = _TextDelta(text)


class _TextDelta:
    type = "text_delta"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseStartEvent:
    """Mimics Anthropic's RawContentBlockStartEvent for a server_tool_use."""

    type = "content_block_start"

    def __init__(self, block_type: str = "server_tool_use") -> None:
        self.content_block = _ContentBlock(block_type)


class _ContentBlock:
    def __init__(self, block_type: str) -> None:
        self.type = block_type


def _text_events(deltas: list[str]) -> list[_TextDeltaEvent]:
    return [_TextDeltaEvent(d) for d in deltas]


class _ToolUseBlock:
    """Mimics Anthropic's ToolUseBlock for test final-message assertions."""

    type = "tool_use"

    def __init__(self, name: str, tool_input: dict[str, Any], tool_id: str = "toolu_1") -> None:
        self.name = name
        self.input = tool_input
        self.id = tool_id

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {"type": "tool_use", "name": self.name, "input": self.input, "id": self.id}


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {"type": "text", "text": self.text}


class _FinalMessage:
    """Return value of `stream.get_final_message()`."""

    def __init__(self, stop_reason: str = "end_turn", content: list[Any] | None = None) -> None:
        self.stop_reason = stop_reason
        self.content = content or []


class _StubStream:
    """Stream stub that iterates a scripted list of structured events.

    Pass `events` as either event objects (.type present) or bare strings
    (treated as text deltas, ergonomic for per-test setup). `final_message`
    is returned from `get_final_message()` at the end of iteration.
    """

    def __init__(
        self,
        events_or_deltas: list[Any],
        final_message: _FinalMessage | None = None,
    ) -> None:
        self._events = [
            e if hasattr(e, "type") else _TextDeltaEvent(e) for e in events_or_deltas
        ]
        self._final = final_message or _FinalMessage()
        self.entered = False

    async def __aenter__(self) -> _StubStream:
        self.entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _gen() -> AsyncIterator[Any]:
            for e in self._events:
                yield e

        return _gen()

    async def get_final_message(self) -> _FinalMessage:
        return self._final


class _StubMessages:
    """Script a sequence of streams. Each `.stream()` call returns the next script.

    `_StubClient(["a", "b"])` is a legacy single-stream shortcut. For
    tool-use loop tests, pass `scripts=[(events1, final1), (events2, final2)]`.
    """

    def __init__(
        self,
        deltas: list[Any] | None = None,
        scripts: list[tuple[list[Any], _FinalMessage | None]] | None = None,
    ) -> None:
        if scripts is not None:
            self._scripts = list(scripts)
        else:
            self._scripts = [((deltas or []), None)]
        self.last_kwargs: dict[str, Any] = {}
        self.stream_count = 0

    def stream(self, **kwargs: Any) -> _StubStream:
        self.last_kwargs = kwargs
        self.stream_count += 1
        if self._scripts:
            events, final = self._scripts.pop(0)
        else:
            events, final = [], None
        return _StubStream(events, final_message=final)


class _StubClient:
    def __init__(
        self,
        deltas: list[Any] | None = None,
        *,
        scripts: list[tuple[list[Any], _FinalMessage | None]] | None = None,
    ) -> None:
        self.messages = _StubMessages(deltas=deltas, scripts=scripts)


async def _collect(gen: AsyncIterator[str]) -> list[str]:
    return [x async for x in gen]


class TestSessionThreading:
    async def test_user_message_appended_before_stream(self) -> None:
        session = InMemorySession()
        client = _StubClient(["Hello there. "])
        state = LlmTurnState()

        async for _ in stream_turn(
            "hi",
            session,
            persona="you are herbert",
            client=client,
            state=state,
        ):
            pass

        roles = [m.role for m in session.messages]
        assert roles == ["user", "assistant"]
        assert session.messages[0].content == "hi"
        assert session.messages[1].content == "Hello there. "

    async def test_persona_passed_as_system(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x."])
        async for _ in stream_turn("hi", session, "PERSONA-TEXT", client=client):
            pass
        assert client.messages.last_kwargs["system"] == "PERSONA-TEXT"

    async def test_prior_history_included_in_messages(self) -> None:
        session = InMemorySession()
        session.append(Message(role="user", content="earlier"))
        session.append(Message(role="assistant", content="earlier reply"))
        client = _StubClient(["ok. "])
        async for _ in stream_turn("now", session, "persona", client=client):
            pass
        msgs = client.messages.last_kwargs["messages"]
        # All prior history + the new user turn, in order
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
        assert msgs[-1]["content"] == "now"


class TestSentenceYield:
    async def test_streams_complete_sentences(self) -> None:
        session = InMemorySession()
        client = _StubClient(["Hel", "lo. ", "How are you?\n"])
        state = LlmTurnState()

        sentences = await _collect(
            stream_turn("hi", session, "p", client=client, state=state)
        )
        assert sentences == ["Hello.", "How are you?"]
        assert state.sentences_yielded == 2

    async def test_unterminated_stream_drains_on_flush(self) -> None:
        session = InMemorySession()
        client = _StubClient(["Hello there"])  # no punctuation ever
        sentences = await _collect(stream_turn("hi", session, "p", client=client))
        assert sentences == ["Hello there"]


class TestTimingState:
    async def test_ttft_populated_on_first_token(self) -> None:
        session = InMemorySession()
        client = _StubClient(["Hi. "])
        state = LlmTurnState()
        async for _ in stream_turn("hi", session, "p", client=client, state=state):
            pass
        assert state.ttft_ms is not None and state.ttft_ms >= 0
        assert state.first_sentence_ms is not None
        assert state.total_ms is not None and state.total_ms >= state.ttft_ms
        assert state.tokens_received == 1
        assert state.accumulated_text == "Hi. "

    async def test_empty_stream_leaves_state_zero(self) -> None:
        session = InMemorySession()
        client = _StubClient([])
        state = LlmTurnState()
        sentences = await _collect(
            stream_turn("hi", session, "p", client=client, state=state)
        )
        assert sentences == []
        assert state.ttft_ms is None
        assert state.tokens_received == 0
        # Assistant message appended even if empty — caller decides whether to pop
        assert session.messages[-1].role == "assistant"
        assert session.messages[-1].content == ""


class TestToolsWiring:
    async def test_no_tools_arg_omits_tools_kwarg(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x. "])
        async for _ in stream_turn("hi", session, "p", client=client):
            pass
        assert "tools" not in client.messages.last_kwargs

    async def test_tools_passed_through_to_stream(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x. "])
        tools = [{"type": "web_search_20250305", "name": "web_search"}]
        async for _ in stream_turn("hi", session, "p", client=client, tools=tools):
            pass
        assert client.messages.last_kwargs["tools"] == tools

    async def test_beta_headers_merged_and_comma_joined(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x. "])
        async for _ in stream_turn(
            "hi",
            session,
            "p",
            client=client,
            beta_headers=["web-fetch-2025-09-10", "code-execution-2025-05-22"],
        ):
            pass
        header = client.messages.last_kwargs["extra_headers"]["anthropic-beta"]
        assert "web-fetch-2025-09-10" in header
        assert "code-execution-2025-05-22" in header

    async def test_beta_headers_and_mcp_header_combine(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x. "])
        mcp = [{"name": "demo", "url": "https://example.com/mcp"}]
        async for _ in stream_turn(
            "hi",
            session,
            "p",
            client=client,
            beta_headers=["code-execution-2025-05-22"],
            mcp_servers=mcp,
        ):
            pass
        header = client.messages.last_kwargs["extra_headers"]["anthropic-beta"]
        # Both tool beta AND MCP beta must appear in the same header value
        assert "code-execution-2025-05-22" in header
        assert "mcp-client-2025-11-20" in header


class TestMcpWiring:
    async def test_empty_mcp_servers_does_not_send_beta_header(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x. "])
        async for _ in stream_turn(
            "hi", session, "p", client=client, mcp_servers=None
        ):
            pass
        assert "mcp_servers" not in client.messages.last_kwargs
        assert "extra_headers" not in client.messages.last_kwargs

    async def test_mcp_servers_present_triggers_beta_header(self) -> None:
        session = InMemorySession()
        client = _StubClient(["x. "])
        mcp = [{"type": "url", "name": "demo", "url": "https://example.com/mcp"}]
        async for _ in stream_turn(
            "hi", session, "p", client=client, mcp_servers=mcp
        ):
            pass
        kwargs = client.messages.last_kwargs
        assert kwargs["mcp_servers"] == mcp
        assert kwargs["extra_headers"] == {"anthropic-beta": "mcp-client-2025-11-20"}


class TestToolUseFiller:
    async def test_tool_use_before_any_text_injects_filler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When server_tool_use starts with zero prior text, we inject a covering sentence."""
        from herbert.llm import claude as claude_mod

        monkeypatch.setattr(claude_mod, "_pick_filler", lambda: "Let me check.")

        session = InMemorySession()
        # Event sequence: tool fires first, then result text arrives
        client = _StubClient(
            [
                _ToolUseStartEvent(),
                _TextDeltaEvent("The weather "),
                _TextDeltaEvent("is 72 and clear."),
            ]
        )
        sentences = await _collect(stream_turn("weather?", session, "p", client=client))
        assert sentences == ["Let me check.", "The weather is 72 and clear."]

    async def test_tool_use_after_text_does_not_inject(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Claude already emitted a covering sentence, don't double up."""
        from herbert.llm import claude as claude_mod

        monkeypatch.setattr(
            claude_mod, "_pick_filler", lambda: "SHOULD_NOT_APPEAR."
        )

        session = InMemorySession()
        client = _StubClient(
            [
                _TextDeltaEvent("One sec. "),
                _ToolUseStartEvent(),
                _TextDeltaEvent("It's 72."),
            ]
        )
        sentences = await _collect(stream_turn("weather?", session, "p", client=client))
        assert sentences == ["One sec.", "It's 72."]

    async def test_multiple_tool_calls_only_first_injects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from herbert.llm import claude as claude_mod

        monkeypatch.setattr(claude_mod, "_pick_filler", lambda: "Looking.")

        session = InMemorySession()
        # Two server tool calls; we only cover the very first one
        client = _StubClient(
            [
                _ToolUseStartEvent(),
                _TextDeltaEvent("In Pasadena "),
                _ToolUseStartEvent(),
                _TextDeltaEvent("it is 72."),
            ]
        )
        sentences = await _collect(stream_turn("weather?", session, "p", client=client))
        assert sentences == ["Looking.", "In Pasadena it is 72."]


class _CapturingDispatcher:
    """Records calls; returns a canned string as the tool_result content."""

    def __init__(self, result: str = "ok") -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self._result = result

    async def execute(
        self, name: str, tool_input: dict[str, Any], turn_id: str | None
    ) -> str:
        self.calls.append((name, tool_input, turn_id))
        return self._result


class TestToolUseLoop:
    async def test_client_side_tool_triggers_second_stream_and_continues(self) -> None:
        """Claude: text + tool_use → we execute → second stream with final text."""
        session = InMemorySession()
        tool_block = _ToolUseBlock(
            name="set_view", tool_input={"mode": "diagnostic"}, tool_id="toolu_view_1"
        )
        scripts = [
            # First request: Claude says a sentence and calls set_view
            (
                [_TextDeltaEvent("Switching now. ")],
                _FinalMessage(
                    stop_reason="tool_use",
                    content=[_TextBlock("Switching now. "), tool_block],
                ),
            ),
            # Second request (after tool_result): Claude finishes normally
            (
                [_TextDeltaEvent("Done.")],
                _FinalMessage(stop_reason="end_turn", content=[_TextBlock("Done.")]),
            ),
        ]
        client = _StubClient(scripts=scripts)
        dispatcher = _CapturingDispatcher()
        state = LlmTurnState()

        sentences = await _collect(
            stream_turn(
                "switch to diagnostic mode",
                session,
                "p",
                client=client,
                local_dispatcher=dispatcher,
                turn_id="turn-abc",
                state=state,
            )
        )

        # Yielded sentences span both streams
        assert sentences == ["Switching now.", "Done."]
        # Dispatcher was called with the right input + turn_id
        assert dispatcher.calls == [("set_view", {"mode": "diagnostic"}, "turn-abc")]
        # Two actual API requests fired
        assert client.messages.stream_count == 2
        # Session ends with one consolidated assistant message
        assert [m.role for m in session.messages] == ["user", "assistant"]
        assert session.messages[-1].content == "Switching now. Done."

    async def test_unknown_tool_name_breaks_loop(self) -> None:
        """If Claude hallucinates a tool we don't know, bail rather than loop."""
        session = InMemorySession()
        tool_block = _ToolUseBlock(
            name="never_heard_of_this", tool_input={}, tool_id="toolu_fake"
        )
        scripts = [
            (
                [_TextDeltaEvent("Working on it.")],
                _FinalMessage(
                    stop_reason="tool_use",
                    content=[_TextBlock("Working on it."), tool_block],
                ),
            ),
        ]
        client = _StubClient(scripts=scripts)
        dispatcher = _CapturingDispatcher()
        state = LlmTurnState()

        sentences = await _collect(
            stream_turn(
                "hi", session, "p", client=client,
                local_dispatcher=dispatcher, turn_id="t1", state=state,
            )
        )
        # We yielded what we got, but the dispatcher never fired and we
        # didn't loop again — one request only.
        assert sentences == ["Working on it."]
        assert dispatcher.calls == []
        assert client.messages.stream_count == 1

    async def test_no_tool_use_single_stream(self) -> None:
        """The common case: no tool_use, no second request."""
        session = InMemorySession()
        scripts = [
            (
                [_TextDeltaEvent("Hi there.")],
                _FinalMessage(stop_reason="end_turn", content=[_TextBlock("Hi there.")]),
            ),
        ]
        client = _StubClient(scripts=scripts)
        dispatcher = _CapturingDispatcher()
        state = LlmTurnState()

        sentences = await _collect(
            stream_turn(
                "hi", session, "p", client=client,
                local_dispatcher=dispatcher, state=state,
            )
        )
        assert sentences == ["Hi there."]
        assert client.messages.stream_count == 1
        assert dispatcher.calls == []

    async def test_second_stream_receives_tool_result_in_messages(self) -> None:
        """The continuation call must carry the assistant tool_use + user tool_result."""
        session = InMemorySession()
        tool_block = _ToolUseBlock(
            name="set_view", tool_input={"mode": "diagnostic"}, tool_id="toolu_xyz"
        )
        scripts = [
            (
                [],
                _FinalMessage(stop_reason="tool_use", content=[tool_block]),
            ),
            (
                [_TextDeltaEvent("ok.")],
                _FinalMessage(stop_reason="end_turn", content=[_TextBlock("ok.")]),
            ),
        ]
        client = _StubClient(scripts=scripts)
        dispatcher = _CapturingDispatcher(result="view set to diagnostic")

        async for _ in stream_turn(
            "flip view", session, "p", client=client,
            local_dispatcher=dispatcher, turn_id="t", state=LlmTurnState(),
        ):
            pass

        # Second request's messages should include: prior user turn,
        # the assistant's tool_use message, and the user's tool_result.
        second_kwargs = client.messages.last_kwargs
        msgs = second_kwargs["messages"]
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "user"]
        # The assistant message echoes the tool_use block
        assistant_msg = msgs[1]
        assert any(
            b.get("type") == "tool_use" and b.get("id") == "toolu_xyz"
            for b in assistant_msg["content"]
        )
        # The final user message carries the tool_result with matching id
        tool_result_msg = msgs[2]
        assert tool_result_msg["content"] == [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_xyz",
                "content": "view set to diagnostic",
            }
        ]


class TestCancellation:
    async def test_cancel_mid_stream_leaves_state_for_cleanup(self) -> None:
        """The orchestrator reads state to decide between pop_last and replace_last."""
        session = InMemorySession()
        client = _StubClient(["The weather is ", "sunny today. ", "Tomorrow."])
        state = LlmTurnState()

        gen = stream_turn("hi", session, "p", client=client, state=state)
        # Pull one sentence then abandon the iterator
        first = await gen.__anext__()
        await gen.aclose()

        assert first == "The weather is sunny today."
        # state tells the orchestrator what happened: we got tokens, not empty
        assert state.tokens_received > 0
        assert state.accumulated_text.startswith("The weather is")
        # Session has the user msg but no assistant append (didn't complete)
        assert [m.role for m in session.messages] == ["user"]
