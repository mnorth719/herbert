"""Tokenizer-artifact repair — patches streaming-delta splits before TTS."""

from __future__ import annotations

from herbert.llm.claude import repair_token_artifacts


class TestObservedFailures:
    """The three patterns Matt reported from a long bedtime-story turn."""

    def test_stranded_single_letter_glues_to_next_word(self) -> None:
        assert (
            repair_token_artifacts(
                "he turned it on out of sheer b oredom."
            )
            == "he turned it on out of sheer boredom."
        )

    def test_space_before_contraction_closes(self) -> None:
        assert (
            repair_token_artifacts("the television wasn 't showing him")
            == "the television wasn't showing him"
        )

    def test_orphan_em_dash_attaches(self) -> None:
        # Em-dash with space-before but no space-after is a common artifact
        assert (
            repair_token_artifacts("every recipe she made was for things he'd been thinking about that very day —butter")
            == "every recipe she made was for things he'd been thinking about that very day—butter"
        )


class TestLegitimateTextPreserved:
    """Guards against over-reach — the repair must not break real prose."""

    def test_article_a_stays_isolated(self) -> None:
        assert repair_token_artifacts("he ate a banana") == "he ate a banana"

    def test_pronoun_i_stays_isolated(self) -> None:
        assert repair_token_artifacts("I think I saw") == "I think I saw"

    def test_em_dash_with_spaces_both_sides_stays(self) -> None:
        # Prose-style em-dash with space on both sides — real usage.
        text = "a beat — then silence"
        assert repair_token_artifacts(text) == text

    def test_contractions_already_closed_unchanged(self) -> None:
        assert repair_token_artifacts("he wasn't going") == "he wasn't going"

    def test_plain_sentence_roundtrips(self) -> None:
        text = "The dodgers won the game by five runs last night."
        assert repair_token_artifacts(text) == text

    def test_empty_input(self) -> None:
        assert repair_token_artifacts("") == ""


class TestApostropheVariants:
    def test_smart_quote_apostrophe_also_fixed(self) -> None:
        # Unicode right-single-quote (U+2019) is what many LLM outputs produce.
        assert (
            repair_token_artifacts("the television wasn \u2019t showing him")
            == "the television wasn\u2019t showing him"
        )

    def test_multiple_contractions_in_one_sentence(self) -> None:
        assert (
            repair_token_artifacts("she 'll say it wasn 't him")
            == "she'll say it wasn't him"
        )
