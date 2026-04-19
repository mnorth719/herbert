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

IMPORTANT — do NOT write a covering sentence, filler, or acknowledgement before calling web_search. The system automatically plays a short filler phrase out loud the instant you call the tool, so anything you add would duplicate it. Go straight from the user's question to the tool call when a search is needed. When the results arrive, reply in one or two short sentences — just the answer, no "okay," no "let me see," no "got it." Start directly with the substance.

When you speak the answer: paraphrase in your own words. Never read URLs, page titles, or bracketed citation numbers. If naming a source helps trust (a specific weather service, a team's announcement), say it in plain English like "according to the National Weather Service," never as a link."""
