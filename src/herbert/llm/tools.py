"""Anthropic server-side tool specs + persona addendum + beta headers.

Server tools are executed by Anthropic — we declare them once and the model
uses them transparently while streaming. For Herbert we enable three:

  web_search      best for general "what's the news about X" questions
  web_fetch       loads a specific URL (pair with search — "go read this page")
  code_execution  Python sandbox for calculations, API calls, date math

Each tool adds latency when it fires, but the capability jump vs. model
knowledge alone is substantial. Cost is per invocation; Claude self-limits
so we don't cap.

If Anthropic deprecates any of these tool IDs, the API returns a clear
error on the first request; bumping the version + beta header is a one-line
fix per tool.
"""

from __future__ import annotations

from typing import Any

# --- Tool specs -------------------------------------------------------------

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
}

WEB_FETCH_TOOL: dict[str, Any] = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
}

CODE_EXECUTION_TOOL: dict[str, Any] = {
    "type": "code_execution_20250522",
    "name": "code_execution",
}

# --- Beta headers -----------------------------------------------------------

# Header value is a comma-separated list; we collect the ones we need and
# join at call time in `build_extra_headers`. Bump any of these when
# Anthropic rolls a tool to a new date.
_BETA_HEADER_WEB_FETCH = "web-fetch-2025-09-10"
_BETA_HEADER_CODE_EXECUTION = "code-execution-2025-05-22"


def build_tools(
    *,
    web_search_enabled: bool,
    web_fetch_enabled: bool = False,
    code_execution_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Assemble the tools list that `messages.stream(tools=...)` wants."""
    tools: list[dict[str, Any]] = []
    if web_search_enabled:
        tools.append(WEB_SEARCH_TOOL)
    if web_fetch_enabled:
        tools.append(WEB_FETCH_TOOL)
    if code_execution_enabled:
        tools.append(CODE_EXECUTION_TOOL)
    return tools


def build_tool_beta_headers(
    *,
    web_fetch_enabled: bool = False,
    code_execution_enabled: bool = False,
) -> list[str]:
    """Return the beta-header tokens tool activation requires.

    The caller merges these with any MCP beta header into one comma-joined
    `anthropic-beta` header value.
    """
    betas: list[str] = []
    if web_fetch_enabled:
        betas.append(_BETA_HEADER_WEB_FETCH)
    if code_execution_enabled:
        betas.append(_BETA_HEADER_CODE_EXECUTION)
    return betas


# --- Persona addendum ------------------------------------------------------

# Appended when any tool is active. Two jobs:
#   1. Teach the model WHICH tool fits WHICH question and how to combine them
#      (search→fetch is the high-accuracy pattern for time-sensitive pages).
#   2. Cover the tool-use latency with a character-in-voice filler that our
#      daemon injects automatically — Claude must NOT add its own filler.
TOOLS_PERSONA_ADDENDUM = """

You have three tools available:

  - web_search: use for current facts drawn from the broader internet — news, sports, weather, stock prices, recent events, anything that changes week-to-week.
  - web_fetch: use when you need the LIVE contents of a specific page — an official schedule, a reference article, documentation. Prefer search→fetch in combination: search to find the authoritative URL, then fetch that URL to get today's actual data. Snippets from search are often cached and stale; fetch bypasses that.
  - code_execution: use for calculations, date and time math, parsing JSON APIs, running short Python (e.g., hitting a free public API like `statsapi.mlb.com` for sports data, `api.weather.gov` for forecasts, or doing arithmetic on numbers).

Pick the lightest tool that answers the question — don't fetch when search alone is enough, don't execute code when a single fetch does it.

IMPORTANT — do NOT write a covering sentence, filler, or acknowledgement before calling any tool. The system automatically plays a short filler out loud the instant you call a tool, so anything you add would duplicate it. Go straight from the user's question to the tool call when one is needed. When the results arrive, reply in one or two short sentences — just the answer, no "okay," no "let me see," no "got it." Start directly with the substance.

When you speak the answer: paraphrase in your own words. Never read URLs, page titles, or bracketed citation numbers. If naming a source helps trust (a specific weather service, a team's announcement), say it in plain English like "according to the MLB schedule," never as a link."""


# Backwards-compat alias — older code imported `WEB_SEARCH_PERSONA_ADDENDUM`
# when only web_search existed. Daemon now uses `TOOLS_PERSONA_ADDENDUM` when
# any tool is active.
WEB_SEARCH_PERSONA_ADDENDUM = TOOLS_PERSONA_ADDENDUM
