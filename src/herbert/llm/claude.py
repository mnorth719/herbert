"""Streaming Claude client + sentence-boundary buffer.

The voice loop's critical latency lever: as soon as Claude emits the first
complete sentence, hand it to TTS so Herbert starts speaking while the rest
of the response is still generating. Target TTFT ≤600ms, first-sentence
≤400ms after that (see plan R6).

Interface shape
---------------

`stream_turn(transcript, session, persona, client, ..., state)` is an async
generator that yields complete sentences as strings. On entry it appends the
user transcript to `session`; on successful completion it appends the full
assistant response. On cancellation or exception, the caller (state machine)
is responsible for session cleanup — `LlmTurnState.tokens_received` and
`.accumulated_text` tell it which path to take (pop the user message if zero
tokens; otherwise replace the assistant message with `"<partial> [interrupted]"`).

Sentence boundary rules
-----------------------

A sentence flushes when `.!?;` appears outside balanced double quotes AND
the next character is whitespace or end-of-buffer. The whitespace check
suppresses false splits on `3.14`, `U.S.A.`, etc. Balanced quote detection
uses straight ASCII `"` only — smart quotes fall through the naive rule,
which is acceptable (they usually flush fine at the OUTER boundary).

Falling back to a 20-word threshold handles the long-winded monologue case
where a model never emits terminal punctuation. `"Dr. Smith"` will split on
the `.` — the plan accepts this as a known failure mode to revisit if it
ever becomes audible.

Tool-use latency filler
-----------------------

Server tools (web_search) can pause generation for several seconds. The
persona asks Claude to emit a covering sentence first, but Haiku sometimes
skips straight to the tool call. When we detect a `server_tool_use` content
block arriving before any text has been emitted, we inject a canned filler
sentence into the output stream so the TTS has something to say while the
search runs. See `_TOOL_USE_FILLERS`.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from herbert.llm.mcp_passthrough import MCP_BETA_HEADER
from herbert.session import Message, Session

log = logging.getLogger(__name__)

_BOUNDARY_CHARS = frozenset(".!?;")

# Canned covering sentences we inject when Claude calls a search tool.
#
# Kept short so TTS lands the audio quickly and the user's ear has something
# to latch onto during the 2-6s search. Each ends with a period so the
# SentenceBuffer flushes immediately on feed(). Voice: dry, retro-futurist,
# cheerfully nerdy — same character Herbert speaks in the rest of the turn.
# Light sprinkling of sci-fi / CS / D&D references; if any start feeling
# stale after weeks of use, edit this tuple (no other code changes needed).
_TOOL_USE_FILLERS: tuple[str, ...] = (
    "Spinning up the modem.",
    "Let me grep the internet.",
    "Rolling a wisdom check.",
    "Pinging the hive mind.",
    "Consulting the ship's computer.",
    "One moment, phoning a friend.",
    "Give me a cycle.",
    "Blowing the dust off the encyclopedia.",
    "Polling the ether.",
    "Interrogating the oracle.",
)

# Tool-use content-block types we treat as "tool is starting". Both
# `server_tool_use` (our use case with web_search) and the legacy
# `tool_use` label are accepted for forward-compat with client tools.
_TOOL_USE_BLOCK_TYPES = frozenset({"server_tool_use", "tool_use"})


# --- Token-boundary artifact repair ----------------------------------------
#
# Claude's BPE tokenizer occasionally splits a word at a subword boundary and
# emits the pieces as separate streaming deltas with spurious whitespace
# between them. The SDK's own `text_stream` concatenates deltas verbatim
# (same as we do), so the artifacts survive into the session content and get
# read aloud by TTS — "b oredom", "wasn 't", "day —butter".
#
# We run a targeted repair at SentenceBuffer flush time. Each pattern below
# fixes one observed failure mode without over-reaching into legitimate text.
# If a repair ever does fire on legitimate content, log an INFO so we can
# tune the pattern.

# Space before a contraction suffix: "wasn 't" -> "wasn't", "it 'll" -> "it'll"
_RE_CONTRACTION = re.compile(
    r"(\w)\s+(['\u2019](?:t|s|d|ll|re|ve|m|em))\b",
    flags=re.IGNORECASE,
)

# Em-dash / en-dash preceded by whitespace when the following side has none:
# "day —butter" -> "day—butter". If both sides have whitespace (real prose
# usage) we leave it alone.
_RE_ORPHAN_EMDASH = re.compile(r"\s+([\u2014\u2013])(?=\w)")

# Single stranded letter between spaces (not 'a' or 'I' which stand alone
# legitimately in English). Glues to the word that follows:
# "sheer b oredom" -> "sheer boredom". The letter must be lower- or
# upper-case but neither "a"/"A" nor "i"/"I"; the following token must be at
# least two lowercase letters so we don't glue to a proper noun.
_RE_STRANDED_LETTER = re.compile(
    r"(\s)([b-hj-zB-HJ-Z])\s+([a-z]{2,})"
)


def repair_token_artifacts(text: str) -> str:
    """Patch known Claude streaming tokenizer artifacts in a completed sentence."""
    before = text
    text = _RE_CONTRACTION.sub(r"\1\2", text)
    text = _RE_ORPHAN_EMDASH.sub(r"\1", text)
    text = _RE_STRANDED_LETTER.sub(r"\1\2\3", text)
    if text != before:
        log.debug("repaired token artifacts: %r -> %r", before, text)
    return text


class LlmClientProtocol(Protocol):
    """The subset of `anthropic.AsyncAnthropic` we depend on. Narrow on purpose — lets
    tests hand in a stub without inheriting the real SDK's surface area."""

    messages: Any


@dataclass
class LlmTurnState:
    """Mutable per-turn tracking written by `stream_turn` and read by the orchestrator.

    Populated in order: `tokens_received` increments on every delta; `ttft_ms`
    fills on the first delta; `first_sentence_ms` fills on the first flush;
    `accumulated_text` grows with every delta; `total_ms` fills on stream end.
    """

    ttft_ms: int | None = None
    first_sentence_ms: int | None = None
    total_ms: int | None = None
    tokens_received: int = 0
    accumulated_text: str = ""
    sentences_yielded: int = 0
    boundary_misses: int = 0  # 20-word fallback fires


# --- Sentence buffer --------------------------------------------------------


@dataclass
class SentenceBuffer:
    """Accumulates text and yields complete sentences on boundaries.

    `feed(text)` returns newly-complete sentences in order. `flush()` drains
    whatever remains as a final sentence (used when the stream ends mid-word).
    """

    word_flush_threshold: int = 20
    _buffer: str = field(default="", init=False)
    _in_quote: bool = field(default=False, init=False)
    force_flush_count: int = field(default=0, init=False)  # how many times the 20-word rule fired

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        return [repair_token_artifacts(s) for s in self._extract_ready()]

    def flush(self) -> list[str]:
        """Return the remaining buffer as one final sentence (if non-empty)."""
        remaining = self._buffer.strip()
        self._buffer = ""
        self._in_quote = False
        return [repair_token_artifacts(remaining)] if remaining else []

    def _extract_ready(self) -> list[str]:
        results: list[str] = []
        # Walk forward finding boundaries outside quote pairs
        while True:
            split_at = self._find_next_boundary()
            if split_at is None:
                break
            sentence = self._buffer[: split_at + 1].strip()
            self._buffer = self._buffer[split_at + 1 :]
            if sentence:
                results.append(sentence)
        # 20-word fallback on whatever remains. Useful for long clauses with
        # no terminal punctuation (rare but we've seen it from streaming models).
        if self._word_count(self._buffer) >= self.word_flush_threshold:
            forced = self._buffer.strip()
            self._buffer = ""
            self._in_quote = False
            self.force_flush_count += 1
            if forced:
                results.append(forced)
        return results

    def _find_next_boundary(self) -> int | None:
        """Return the index of the next sentence-ending boundary, or None."""
        for i, ch in enumerate(self._buffer):
            if ch == '"':
                self._in_quote = not self._in_quote
                continue
            if self._in_quote:
                continue
            if ch in _BOUNDARY_CHARS:
                # Only a real boundary if followed by whitespace or end of buffer
                if i + 1 >= len(self._buffer):
                    # Wait for more input — next char might be punctuation or digit
                    return None
                if self._buffer[i + 1].isspace():
                    return i
        return None

    @staticmethod
    def _word_count(text: str) -> int:
        return len(text.split())


# --- Streaming entry point ---------------------------------------------------


def _build_stream_kwargs(
    session: Session,
    persona: str,
    model: str,
    max_tokens: int,
    mcp_servers: list[dict[str, str]] | None,
    tools: list[dict[str, Any]] | None,
    beta_headers: list[str] | None,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Split the call into (stream kwargs, extra headers).

    The `anthropic-beta` header value is a comma-separated list of tokens.
    We collect every token required by the active tools + MCP config into
    one header so the caller doesn't have to juggle multiple headers.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": persona,
        "messages": [{"role": m.role, "content": m.content} for m in session.messages],
    }
    if tools:
        kwargs["tools"] = tools
    betas: list[str] = list(beta_headers or [])
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers
        if MCP_BETA_HEADER not in betas:
            betas.append(MCP_BETA_HEADER)
    extra_headers: dict[str, str] | None = None
    if betas:
        extra_headers = {"anthropic-beta": ",".join(betas)}
    return kwargs, extra_headers


async def stream_turn(
    transcript: str,
    session: Session,
    persona: str,
    client: LlmClientProtocol,
    *,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 1024,
    mcp_servers: list[dict[str, str]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    beta_headers: list[str] | None = None,
    state: LlmTurnState | None = None,
    word_flush_threshold: int = 20,
) -> AsyncIterator[str]:
    """Stream Claude's response, yielding complete sentences as they form.

    Side effects on `session`:
      - user `Message` appended at the top of the call
      - assistant `Message` appended after the stream ends normally

    On cancellation the caller is responsible for reconciling the session
    (pop the user message if `state.tokens_received == 0`, otherwise replace
    the assistant message with a partial + `[interrupted]` marker).
    """
    session.append(Message(role="user", content=transcript))
    kwargs, extra_headers = _build_stream_kwargs(
        session, persona, model, max_tokens, mcp_servers, tools, beta_headers
    )
    buffer = SentenceBuffer(word_flush_threshold=word_flush_threshold)
    start = time.perf_counter()

    # Anthropic's async SDK: `messages.stream()` returns an async context manager.
    # When MCP beta headers are needed we pass them via `extra_headers`, which the
    # SDK forwards to the HTTP request.
    stream_factory = client.messages.stream
    if extra_headers:
        stream_cm = stream_factory(**kwargs, extra_headers=extra_headers)
    else:
        stream_cm = stream_factory(**kwargs)

    any_text_emitted = False

    async with stream_cm as stream:
        async for event in stream:
            event_type = getattr(event, "type", None)

            # Tool starting with zero preceding text — inject a covering
            # sentence locally so the TTS has something to say during the
            # search. Fires at most once per turn (guarded by any_text_emitted).
            if (
                event_type == "content_block_start"
                and _is_tool_use_start(event)
                and not any_text_emitted
            ):
                filler = _pick_filler()
                log.info("injecting tool-use filler: %r", filler)
                async for sentence in _feed_text_into_buffer(
                    filler + " ", buffer, state, start
                ):
                    any_text_emitted = True
                    yield sentence
                continue

            # Text delta — normal streaming path
            if event_type == "content_block_delta":
                delta_obj = getattr(event, "delta", None)
                if getattr(delta_obj, "type", None) == "text_delta":
                    text = getattr(delta_obj, "text", "") or ""
                    if not text:
                        continue
                    async for sentence in _feed_text_into_buffer(
                        text, buffer, state, start
                    ):
                        any_text_emitted = True
                        yield sentence

    # Flush any trailing text that never hit a boundary (rare but real)
    for sentence in buffer.flush():
        if state is not None:
            state.sentences_yielded += 1
            if state.first_sentence_ms is None:
                state.first_sentence_ms = _elapsed_ms(start)
        yield sentence

    if state is not None:
        state.total_ms = _elapsed_ms(start)
        state.boundary_misses = buffer.force_flush_count
        # Append the full assistant response to the session on clean completion
        session.append(Message(role="assistant", content=state.accumulated_text))
    else:
        # Caller didn't provide state; we still need to close the session cleanly.
        # Rebuild assistant content from nothing — not ideal but only matters for
        # tests that skip `state`. Production callers always pass one.
        pass


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _is_tool_use_start(event: Any) -> bool:
    """Return True if `event` is a `content_block_start` for a tool-use block."""
    block = getattr(event, "content_block", None)
    block_type = getattr(block, "type", None)
    return block_type in _TOOL_USE_BLOCK_TYPES


def _pick_filler() -> str:
    """Pick a covering sentence at random. Module-level so tests can patch."""
    return random.choice(_TOOL_USE_FILLERS)


async def _feed_text_into_buffer(
    text: str,
    buffer: SentenceBuffer,
    state: LlmTurnState | None,
    start: float,
) -> AsyncIterator[str]:
    """Common token-handling path: update state, flush sentences via buffer.

    Extracted so the event loop can use the same bookkeeping for real text
    deltas AND for locally-injected tool-use fillers.
    """
    now_ms = _elapsed_ms(start)
    if state is not None:
        state.tokens_received += 1
        state.accumulated_text += text
        if state.ttft_ms is None:
            state.ttft_ms = now_ms
    for sentence in buffer.feed(text):
        if state is not None:
            state.sentences_yielded += 1
            if state.first_sentence_ms is None:
                state.first_sentence_ms = _elapsed_ms(start)
        yield sentence
