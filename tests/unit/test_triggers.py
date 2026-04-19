"""Diagnostic-mode trigger matcher."""

from __future__ import annotations

import pytest

from herbert.diagnostic.triggers import match


class TestEnterTriggers:
    @pytest.mark.parametrize(
        "transcript",
        [
            "herbert show me the logs",
            "Herbert show me the logs",
            "HERBERT SHOW ME THE LOGS",
            "herbert show me the logs.",
            "herbert show me the logs!",
            "  herbert show me the logs  ",
            "herbert  show  me  the  logs",  # collapses whitespace
            "show me the logs",
            "diagnostic mode",
            "herbert diagnostic mode",
            "enter diagnostic mode",
            "show me diagnostics",
        ],
    )
    def test_phrases_enter_diagnostic(self, transcript: str) -> None:
        assert match(transcript) == "enter_diagnostic"


class TestExitTriggers:
    @pytest.mark.parametrize(
        "transcript",
        [
            "exit diagnostic",
            "exit diagnostic mode",
            "herbert exit diagnostic",
            "character mode",
            "herbert character mode",
            "back to character",
            "hide the logs",
            "herbert hide the logs.",
        ],
    )
    def test_phrases_exit_diagnostic(self, transcript: str) -> None:
        assert match(transcript) == "exit_diagnostic"


class TestFalsePositives:
    @pytest.mark.parametrize(
        "transcript",
        [
            # Whole-utterance rule: partial wrapping must not trigger
            "herbert show me the logs from yesterday",
            "herbert show me the logs for the deploy",
            "can you show me the logs",
            "i said diagnostic mode yesterday",
            "what is diagnostic mode",
            # Unrelated text
            "what's the weather",
            "tell me a story",
            "how are you",
            # Empty / whitespace-only
            "",
            "   ",
        ],
    )
    def test_non_triggers_return_none(self, transcript: str) -> None:
        assert match(transcript) is None


class TestNormalization:
    def test_multiple_trailing_punctuation(self) -> None:
        assert match("diagnostic mode...") == "enter_diagnostic"
        assert match("exit diagnostic?!") == "exit_diagnostic"

    def test_newline_is_whitespace(self) -> None:
        assert match("herbert\nshow\nme\nthe\nlogs") == "enter_diagnostic"
