"""Session-close extractor: Claude-call wrapper for (summary, new_facts).

These tests inject a stub `messages.create(...)` — no real Anthropic call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from herbert.memory.extractor import extract_session_summary


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, text: str) -> None:
        self.content = [_TextBlock(text)]


class _StubMessages:
    """Records calls + returns scripted outcomes.

    ``outcomes`` is an ordered list; each entry is either an ``_Response``
    (happy path) or an ``Exception`` (simulated failure). Each call pops
    the next outcome in order.
    """

    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self._outcomes = list(outcomes or [])
        self.call_count = 0
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _Response:
        self.last_kwargs = kwargs
        if self.call_count >= len(self._outcomes):
            raise RuntimeError("stub ran out of scripted outcomes")
        outcome = self._outcomes[self.call_count]
        self.call_count += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _StubClient:
    def __init__(self, messages: _StubMessages) -> None:
        self.messages = messages


def _client_with(text: str) -> _StubClient:
    return _StubClient(_StubMessages(outcomes=[_Response(text)]))


class TestHappyPath:
    async def test_returns_summary_and_new_facts(self) -> None:
        client = _client_with(
            json.dumps({"summary": "chatted about Lakers", "new_facts": ["Matt is a Dodgers fan"]})
        )
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "I'm a Dodgers fan"), ("assistant", "noted")],
            existing_facts=[],
        )
        assert summary == "chatted about Lakers"
        assert facts == ["Matt is a Dodgers fan"]

    async def test_filters_already_known_facts(self) -> None:
        """If Claude returns a fact that already exists in existing_facts,
        the extractor dedups defensively so the caller doesn't double-save."""
        client = _client_with(
            json.dumps({
                "summary": "restated some things",
                "new_facts": ["Matt lives in Upland", "Matt has a sister"],
            })
        )
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x"), ("assistant", "y")],
            existing_facts=["Matt lives in Upland"],
        )
        assert summary == "restated some things"
        assert facts == ["Matt has a sister"]

    async def test_empty_new_facts_still_returns_summary(self) -> None:
        client = _client_with(
            json.dumps({"summary": "brief chitchat", "new_facts": []})
        )
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "hi"), ("assistant", "hey")],
            existing_facts=[],
        )
        assert summary == "brief chitchat"
        assert facts == []


class TestEmptyTurns:
    async def test_empty_turns_short_circuits_no_client_call(self) -> None:
        stub = _StubMessages(outcomes=[])
        client = _StubClient(stub)
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[],
            existing_facts=[],
        )
        assert summary is None
        assert facts == []
        assert stub.call_count == 0


class TestFailurePaths:
    async def test_malformed_json_returns_none_empty(self) -> None:
        client = _client_with("this is not json {incomplete")
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x")],
            existing_facts=[],
        )
        assert summary is None
        assert facts == []

    async def test_missing_summary_key_returns_none_empty(self) -> None:
        client = _client_with(json.dumps({"new_facts": ["fact a"]}))  # missing summary
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x")],
            existing_facts=[],
        )
        assert summary is None
        assert facts == []

    async def test_missing_new_facts_key_returns_none_empty(self) -> None:
        client = _client_with(json.dumps({"summary": "ok"}))  # missing new_facts
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x")],
            existing_facts=[],
        )
        assert summary is None
        assert facts == []

    async def test_retries_once_on_transient_error(self) -> None:
        """First call raises; second call succeeds — extractor returns the
        second response, not (None, [])."""
        stub = _StubMessages(
            outcomes=[
                RuntimeError("transient"),
                _Response(json.dumps({"summary": "ok", "new_facts": []})),
            ]
        )
        client = _StubClient(stub)
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x")],
            existing_facts=[],
        )
        assert summary == "ok"
        assert facts == []
        assert stub.call_count == 2

    async def test_second_failure_returns_none_empty(self) -> None:
        stub = _StubMessages(
            outcomes=[RuntimeError("transient-1"), RuntimeError("transient-2")]
        )
        client = _StubClient(stub)
        summary, facts = await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x")],
            existing_facts=[],
        )
        assert summary is None
        assert facts == []
        assert stub.call_count == 2


class TestRequestShape:
    async def test_request_uses_expected_model_and_max_tokens(self) -> None:
        stub = _StubMessages(
            outcomes=[_Response(json.dumps({"summary": "x", "new_facts": []}))]
        )
        client = _StubClient(stub)
        await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "x")],
            existing_facts=[],
        )
        assert stub.last_kwargs is not None
        assert stub.last_kwargs["model"] == "claude-sonnet-4-6"
        assert stub.last_kwargs["max_tokens"] <= 1024  # summaries + facts are short

    async def test_request_includes_turns_in_user_message(self) -> None:
        stub = _StubMessages(
            outcomes=[_Response(json.dumps({"summary": "x", "new_facts": []}))]
        )
        client = _StubClient(stub)
        await extract_session_summary(
            client=client,
            model="claude-sonnet-4-6",
            turns=[("user", "hello"), ("assistant", "hi back")],
            existing_facts=[],
        )
        # The turns should be serialised into the user message content
        messages = stub.last_kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert "hello" in content
        assert "hi back" in content


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real retry sleep to keep the test suite fast."""

    async def _nosleep(_: float) -> None:
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
