"""Tool specs + persona addendum + beta headers.

Two families of tools, same `tools=[...]` kwarg on the request:

  Server-side (Anthropic executes them transparently while streaming):
    web_search        best for general "what's the news about X" questions
    web_fetch         loads a specific URL (pair with search — "go read this page")
    code_execution    Python sandbox for calculations, API calls, date math

  Client-side (we execute them via LocalToolDispatcher and feed tool_result
  back to continue the stream):
    set_view          switch the frontend between character and diagnostic view

Server tools add latency when they fire but cost nothing in Herbert code.
Client tools cost a round-trip per call but let us do local side effects.
Claude self-limits either way.
"""

from __future__ import annotations

from typing import Any

from herbert.llm.local_tools import ALL_LOCAL_TOOLS

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
    include_local_tools: bool = True,
) -> list[dict[str, Any]]:
    """Assemble the tools list that `messages.stream(tools=...)` wants.

    Server tools come first (Anthropic-executed), then client-side local
    tools (we execute via LocalToolDispatcher). Order doesn't affect
    behavior but keeps the list readable in logs.
    """
    tools: list[dict[str, Any]] = []
    if web_search_enabled:
        tools.append(WEB_SEARCH_TOOL)
    if web_fetch_enabled:
        tools.append(WEB_FETCH_TOOL)
    if code_execution_enabled:
        tools.append(CODE_EXECUTION_TOOL)
    if include_local_tools:
        tools.extend(ALL_LOCAL_TOOLS)
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

You have four tools available:

  - web_search: use for current facts drawn from the broader internet — news, sports, weather, stock prices, recent events, anything that changes week-to-week.
  - web_fetch: use when you need the LIVE contents of a specific page — an official schedule, a reference article, documentation. Prefer search→fetch in combination: search to find the authoritative URL, then fetch that URL to get today's actual data. Snippets from search are often cached and stale; fetch bypasses that.
  - code_execution: use for calculations, date and time math, parsing JSON APIs, running short Python (e.g., hitting a free public API like `statsapi.mlb.com` for sports data, `api.weather.gov` for forecasts, or doing arithmetic on numbers).
  - set_view: switch the frontend display between 'character' and 'diagnostic'. Call with mode='diagnostic' when Matt asks to see the logs / enter diagnostic mode / debug mode / see the innards — interpret intent, wording varies because of transcription errors. Call with mode='character' when he wants the normal view back. Do NOT call this for factual questions like "show me the logs from yesterday" (that's asking about log content, not switching views).

Pick the lightest tool that answers the question — don't fetch when search alone is enough, don't execute code when a single fetch does it.

IMPORTANT — do NOT write a covering sentence, filler, or acknowledgement before calling web_search, web_fetch, or code_execution. The system automatically plays a short filler out loud the instant you call a network tool, so anything you add would duplicate it. Go straight from the user's question to the tool call when one is needed. When the results arrive, reply in one or two short sentences — just the answer, no "okay," no "let me see," no "got it." Start directly with the substance.

set_view is local and instant — no filler fires — so a brief acknowledgement ("switching now" or similar) before or after is fine if it reads natural, but you can also just silently flip.

When you speak search / fetch / code answers: paraphrase in your own words. Never read URLs, page titles, or bracketed citation numbers. If naming a source helps trust (a specific weather service, a team's announcement), say it in plain English like "according to the MLB schedule," never as a link."""


# Backwards-compat alias — older code imported `WEB_SEARCH_PERSONA_ADDENDUM`
# when only web_search existed. Daemon now uses `TOOLS_PERSONA_ADDENDUM` when
# any tool is active.
WEB_SEARCH_PERSONA_ADDENDUM = TOOLS_PERSONA_ADDENDUM
