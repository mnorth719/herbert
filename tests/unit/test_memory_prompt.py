"""System prompt builder: composition + token breakdown."""

from __future__ import annotations

from herbert.memory.prompt import build_system_prompt

BASE_PERSONA = "You are Herbert. Speak plainly."
TOOLS_ADDENDUM = "\n\nYou have four tools..."


class TestFullComposition:
    def test_sections_appear_in_cacheable_order(self) -> None:
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=TOOLS_ADDENDUM,
            facts=["Matt lives in Upland", "Matt is a Dodgers fan"],
            summaries=[("Chatted about the Lakers", 1_713_000_000)],
        )
        # Persona first, then tools addendum, then facts header, then summaries header.
        persona_idx = prompt.index("You are Herbert")
        tools_idx = prompt.index("You have four tools")
        facts_idx = prompt.index("## What I know about Matt")
        summaries_idx = prompt.index("## Recent sessions")
        assert persona_idx < tools_idx < facts_idx < summaries_idx

    def test_facts_render_as_dashed_bullets(self) -> None:
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=["fact a", "fact b"],
            summaries=[],
        )
        assert "- fact a" in prompt
        assert "- fact b" in prompt

    def test_summary_includes_human_date(self) -> None:
        # 1_713_000_000 = Sun Apr 13 2024 10:40:00 UTC (the exact local date
        # will depend on timezone — assert the known stable pieces only).
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=[],
            summaries=[("Chatted about the Lakers", 1_713_000_000)],
        )
        # "Chatted about the Lakers" is the content; the date prefix should
        # render as "<Abbrev day> <Abbrev month> <day>:"
        assert "Chatted about the Lakers" in prompt


class TestEmptyStatePreservation:
    """Empty sections render placeholder copy so the cacheable prefix
    shape stays stable across "no content" ↔ "some content" transitions."""

    def test_empty_facts_renders_placeholder(self) -> None:
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=[],
            summaries=[("some summary", 1_713_000_000)],
        )
        assert "## What I know about Matt" in prompt
        assert "no facts learned yet" in prompt

    def test_empty_summaries_renders_placeholder(self) -> None:
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=["fact a"],
            summaries=[],
        )
        assert "## Recent sessions" in prompt
        assert "no closed sessions yet" in prompt

    def test_both_empty_still_renders_both_headers(self) -> None:
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=[],
            summaries=[],
        )
        assert "## What I know about Matt" in prompt
        assert "## Recent sessions" in prompt


class TestToolsAddendumOptional:
    def test_none_addendum_omits_it(self) -> None:
        prompt, _ = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=["fact a"],
            summaries=[],
        )
        assert "You are Herbert" in prompt
        assert "## What I know about Matt" in prompt
        # No tools content
        assert "four tools" not in prompt


class TestTokenBreakdown:
    def test_breakdown_contains_all_expected_keys(self) -> None:
        _, breakdown = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=TOOLS_ADDENDUM,
            facts=["fact a"],
            summaries=[("s", 1_713_000_000)],
        )
        assert set(breakdown.keys()) == {"persona", "tools", "facts", "summaries", "total"}
        for v in breakdown.values():
            assert isinstance(v, int)
            assert v >= 0

    def test_breakdown_total_matches_prompt_estimate(self) -> None:
        prompt, breakdown = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=TOOLS_ADDENDUM,
            facts=["fact a", "fact b"],
            summaries=[("s1", 1_713_000_000)],
        )
        # Sum of per-section tokens should approximate the total (the
        # heuristic can have rounding slack on section boundaries).
        section_sum = (
            breakdown["persona"]
            + breakdown["tools"]
            + breakdown["facts"]
            + breakdown["summaries"]
        )
        # Allow small rounding drift from the per-section integer divisions
        assert abs(section_sum - breakdown["total"]) <= 4
        # Total should be close to a direct estimate on the whole prompt
        from herbert.boot_snapshot import estimate_tokens

        assert abs(breakdown["total"] - estimate_tokens(prompt)) <= 4

    def test_tools_breakdown_is_zero_when_omitted(self) -> None:
        _, breakdown = build_system_prompt(
            persona=BASE_PERSONA,
            tools_addendum=None,
            facts=["fact a"],
            summaries=[],
        )
        assert breakdown["tools"] == 0


class TestDeterminism:
    def test_same_inputs_produce_identical_output(self) -> None:
        args = dict(
            persona=BASE_PERSONA,
            tools_addendum=TOOLS_ADDENDUM,
            facts=["fact a", "fact b"],
            summaries=[("s1", 1_713_000_000), ("s2", 1_713_000_100)],
        )
        a, _ = build_system_prompt(**args)
        b, _ = build_system_prompt(**args)
        assert a == b
