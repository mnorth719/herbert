"""Anthropic server-side tool specs + persona addendum.

Server tools (web_search, code_execution, etc.) are executed by Anthropic —
we declare them once and the model uses them transparently while streaming.
For Herbert v1 we enable only web_search, which unlocks current-information
questions ("what's the weather in Pasadena", "who won the game last night").

The tool ID is pinned here so a version bump is a one-line edit. If
Anthropic deprecates `web_search_20250305`, the model will return a clear
error on the first request — we'd see it immediately in the logs.
"""

from __future__ import annotations

from typing import Any

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
}


def build_tools(*, web_search_enabled: bool) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if web_search_enabled:
        tools.append(WEB_SEARCH_TOOL)
    return tools


# Appended to the persona when web_search is active. Two jobs:
#   1. Teach the model WHEN to reach for the tool and HOW to speak its
#      results (TTS-friendly: no URLs, no citation brackets, one-to-two
#      sentences, paraphrase not read).
#   2. Cover the 2-6 second search latency with character instead of dead
#      air — Claude emits a short in-voice acknowledgement BEFORE calling
#      the tool, which streams to TTS immediately via sentence-boundary
#      flushing. By the time Herbert finishes saying "hold on a second",
#      the search has returned and the real answer streams in.
WEB_SEARCH_PERSONA_ADDENDUM = """

You have a web_search tool. Reach for it when the answer depends on anything current — weather, news, sports scores, flight status, stock prices, recent events, anything that could have changed this week. Don't use it for general knowledge you already have.

IMPORTANT — before calling web_search, ALWAYS emit a short in-character acknowledgement as its own complete sentence (end it with a period). This plays through the speaker while the search runs, so the wait never feels dead. Vary the phrasing; don't repeat yourself across turns. Examples in the right voice:

- "Hold on, let me check."
- "One tick, I'll look that up."
- "Just a moment. Consulting the network."
- "Give me a second, going to pull that from the wire."
- "Let me see."

Then do the search. Then answer in one or two short sentences. Paraphrase in your own words — never read back URLs, page titles, or bracketed citation numbers. If naming a source helps trust (a specific weather service, a team's announcement), say it in plain English like "according to the National Weather Service," not as a link. Do NOT repeat your acknowledgement at the end; once the data's in, just give the answer."""
