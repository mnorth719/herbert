"""Session-close extractor — the second Claude call.

When a session closes (5 min of inactivity), the daemon schedules an
async task that feeds the session's turns into this function. The
return value is either ``(summary, new_facts)`` or ``(None, [])`` on any
failure. It never raises — extraction is a best-effort enrichment and
must not be able to brick the next session's start.

Design notes:

- Non-streaming ``client.messages.create``. We want the whole response
  at once and don't care about TTFT here — Claude's response is small
  (a summary sentence and a handful of short fact lines).
- Structured output via a JSON envelope in the response text. We don't
  use tool-use or structured-output mode; a hardcoded "respond with
  JSON" system prompt and a defensive ``json.loads`` is enough for v1.
- One retry on any exception with a 500 ms backoff. Second failure
  returns ``(None, [])`` silently — the session is already sealed and
  will just not contribute a summary to future prompts.
- Existing facts are passed in so Claude can dedupe intent-wise. We
  also run a set-based filter on the way out to defend against Claude
  returning an exact duplicate anyway.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)

_MAX_TOKENS = 512
_RETRY_DELAY_S = 0.5

_SYSTEM_PROMPT = """You are a summariser for a voice assistant's session log.

Your job has two parts:

1. Produce a short, concrete summary of what the user and the assistant talked about. One or two sentences. Include named entities, dates, and topics. This summary is used later for continuity ("last Thursday you and I chatted about X").

2. Extract any new durable facts about the user that are worth remembering across sessions — their name, where they live, people in their life, strong preferences, identity-level things. Short declarative sentences. DO NOT include transient things (one-off questions, what the weather was) or anything already in the "Existing facts" list.

Return ONLY a valid JSON object with this exact shape, no other text:

{"summary": "...", "new_facts": ["...", "..."]}

If there are no new facts worth saving, return an empty list: "new_facts": [].
If the conversation was too trivial to summarise, return an empty string: "summary": "".
"""


class _MessagesCreate(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class _ClientProtocol(Protocol):
    messages: _MessagesCreate


async def extract_session_summary(
    *,
    client: _ClientProtocol,
    model: str,
    turns: list[tuple[str, str]],
    existing_facts: list[str],
) -> tuple[str | None, list[str]]:
    """Return ``(summary, new_facts)`` for a closed session.

    ``turns`` is the list of ``(role, content)`` pairs for the session,
    oldest first — the shape ``MemoryStore.get_session_turns`` returns.
    ``existing_facts`` is what's currently in the facts table, used for
    dedup on the way out.
    """
    if not turns:
        return None, []

    user_body = _serialise_turns(turns, existing_facts)

    response: Any = None
    for attempt in range(2):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_body}],
            )
            break
        except Exception as exc:
            log.warning(
                "memory extraction call failed (attempt %d): %s", attempt + 1, exc
            )
            if attempt == 0:
                await asyncio.sleep(_RETRY_DELAY_S)
                continue
            return None, []

    text = _response_text(response)
    parsed = _parse_envelope(text)
    if parsed is None:
        return None, []

    summary, new_facts = parsed
    # Normalise: empty string → None so downstream NULL-filter in
    # get_recent_summaries does the right thing.
    if not summary:
        summary = None
    # Defensive dedup: drop any fact already known.
    known = {f.strip().lower() for f in existing_facts}
    deduped = [f for f in new_facts if f.strip().lower() not in known]
    return summary, deduped


def _serialise_turns(
    turns: list[tuple[str, str]], existing_facts: list[str]
) -> str:
    """Turn the session into a compact prompt body for Claude."""
    lines = ["Conversation:"]
    for role, content in turns:
        lines.append(f"{role}: {content}")
    lines.append("")
    if existing_facts:
        lines.append("Existing facts (do not repeat):")
        for f in existing_facts:
            lines.append(f"- {f}")
    else:
        lines.append("Existing facts (do not repeat): none")
    return "\n".join(lines)


def _response_text(response: Any) -> str:
    """Pull the text out of the first text block in a ``Message`` response.

    The Anthropic SDK returns content blocks; we expect the first to be
    text for our structured-JSON output flavour. Defensively fall back to
    an empty string so the parse step fails cleanly rather than raising.
    """
    try:
        blocks = response.content or []
        for block in blocks:
            if getattr(block, "type", None) == "text":
                return getattr(block, "text", "") or ""
    except Exception as exc:
        log.warning("memory extraction response shape unexpected: %s", exc)
    return ""


def _parse_envelope(text: str) -> tuple[str, list[str]] | None:
    """Parse the JSON envelope, returning None on any failure."""
    if not text.strip():
        log.warning("memory extraction returned empty text")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("memory extraction JSON parse failed: %s", exc)
        return None
    if not isinstance(data, dict):
        log.warning("memory extraction returned non-object JSON: %r", type(data).__name__)
        return None
    if "summary" not in data or "new_facts" not in data:
        log.warning(
            "memory extraction JSON missing required keys: %s", sorted(data.keys())
        )
        return None
    summary = data["summary"]
    facts = data["new_facts"]
    if not isinstance(summary, str) or not isinstance(facts, list):
        log.warning("memory extraction JSON has wrong value types")
        return None
    # Ensure facts are strings; drop anything else defensively.
    clean_facts = [f for f in facts if isinstance(f, str) and f.strip()]
    return summary, clean_facts
