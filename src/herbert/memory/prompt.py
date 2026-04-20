"""System prompt assembly with memory sections.

Shared by two callers: the daemon's per-turn persona resolution and the
``/api/prompt/snapshot`` endpoint (so they can never drift — what Claude
sees and what Matt inspects must be the same string).

Ordering (persona → tools addendum → facts → summaries) is chosen to
keep Anthropic's prompt-prefix cache warm within a ~5 min burst. Facts
and summaries only change at session close, which is already a natural
cache-miss boundary.

Empty sections render placeholder copy (``_(no facts learned yet)_``)
rather than disappearing entirely, so a "first turn with no memory"
and a "fifth turn with some memory" share the same section shape. That
keeps the cacheable prefix stable in edge cases Matt is likely to hit
the first week of use.
"""

from __future__ import annotations

import time

from herbert.boot_snapshot import estimate_tokens

_FACTS_HEADER = "## What I know about Matt"
_SUMMARIES_HEADER = "## Recent sessions"
_NO_FACTS_PLACEHOLDER = "_(no facts learned yet)_"
_NO_SUMMARIES_PLACEHOLDER = "_(no closed sessions yet)_"


def build_system_prompt(
    *,
    persona: str,
    tools_addendum: str | None,
    facts: list[str],
    summaries: list[tuple[str, int]],
) -> tuple[str, dict[str, int]]:
    """Assemble the full system prompt + a per-section token estimate.

    The estimate uses the same cheap 4-chars-per-token heuristic as
    ``boot_snapshot.estimate_tokens`` — good enough for drift detection
    and UI display, not a substitute for a real tokenizer.
    """
    persona_block = persona.rstrip()
    tools_block = tools_addendum or ""

    facts_body = _render_facts(facts)
    facts_block = f"\n\n{_FACTS_HEADER}\n{facts_body}"

    summaries_body = _render_summaries(summaries)
    summaries_block = f"\n\n{_SUMMARIES_HEADER}\n{summaries_body}"

    full = persona_block + tools_block + facts_block + summaries_block

    breakdown = {
        "persona": estimate_tokens(persona_block),
        "tools": estimate_tokens(tools_block) if tools_block else 0,
        "facts": estimate_tokens(facts_block),
        "summaries": estimate_tokens(summaries_block),
        "total": estimate_tokens(full),
    }
    return full, breakdown


def _render_facts(facts: list[str]) -> str:
    if not facts:
        return _NO_FACTS_PLACEHOLDER
    return "\n".join(f"- {f}" for f in facts)


def _render_summaries(summaries: list[tuple[str, int]]) -> str:
    if not summaries:
        return _NO_SUMMARIES_PLACEHOLDER
    lines = []
    for summary, ended_at in summaries:
        label = time.strftime("%a %b %-d", time.localtime(ended_at))
        lines.append(f"- {label}: {summary}")
    return "\n".join(lines)
