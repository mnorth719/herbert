"""stream_turn: session threading + TTFT tracking + MCP header shape.

These tests inject a stub `messages.stream(...)` context manager — we never
talk to the real Anthropic SDK here. The live smoke test lives in
`tests/integration/test_llm_live.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from herbert.llm.claude import LlmTurnState, stream_turn
from herbert.session import InMemorySession, Message


class _StubStream:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.entered = False

    async def __aenter__(self) -> _StubStream:
        self.entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            for d in self._deltas:
                yield d

        return _gen()


class _StubMessages:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.last_kwargs: dict[str, Any] = {}

    def stream(self, **kwargs: Any) -> _StubStream:
        self.last_kwargs = kwargs
        return _StubStream(self._deltas)


class _StubClient:
    def __init__(self, deltas: list[str]) -> None:
        self.messages = _StubMessages(deltas)


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
