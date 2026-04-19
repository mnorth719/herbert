"""SentenceBuffer: the boundary-flush logic that drives LLM → TTS handoff."""

from __future__ import annotations

from herbert.llm.claude import SentenceBuffer


class TestBasicBoundaries:
    def test_period_flushes_on_boundary_with_space(self) -> None:
        buf = SentenceBuffer()
        # "Hello there. " — flushes on the space that follows the period
        assert buf.feed("Hello") == []
        assert buf.feed(" there.") == []  # no trailing whitespace yet
        out = buf.feed(" ")
        assert out == ["Hello there."]

    def test_question_mark_flushes(self) -> None:
        buf = SentenceBuffer()
        assert buf.feed("Are you ok? ") == ["Are you ok?"]

    def test_exclamation_flushes(self) -> None:
        buf = SentenceBuffer()
        assert buf.feed("Stop! ") == ["Stop!"]

    def test_semicolon_flushes(self) -> None:
        buf = SentenceBuffer()
        assert buf.feed("First; ") == ["First;"]

    def test_multiple_sentences_in_one_feed(self) -> None:
        buf = SentenceBuffer()
        out = buf.feed("Hello. How are you? Good.\n")
        assert out == ["Hello.", "How are you?", "Good."]


class TestFlushRemaining:
    def test_explicit_flush_emits_tail(self) -> None:
        buf = SentenceBuffer()
        buf.feed("Hello there")
        assert buf.flush() == ["Hello there"]
        assert buf.flush() == []  # empty after drain

    def test_unterminated_stream_drains_on_flush(self) -> None:
        buf = SentenceBuffer()
        buf.feed("Hello. Unfinished")
        # Only the complete sentence has flushed; "Unfinished" waits for flush()
        assert buf.flush() == ["Unfinished"]


class TestFalsePositives:
    def test_decimal_does_not_flush(self) -> None:
        buf = SentenceBuffer()
        # "Pi is 3.14." — the period after "3" is followed by "1", not space
        assert buf.feed("Pi is 3.14. ") == ["Pi is 3.14."]

    def test_inline_abbreviations_dont_split(self) -> None:
        """Periods that aren't followed by whitespace never flush ('U.S.A' → no split)."""
        buf = SentenceBuffer()
        # No trailing whitespace after the final 'A' so internal periods stay put
        assert buf.feed("I live in U.S.A") == []

    def test_abbreviation_followed_by_space_splits_known_limitation(self) -> None:
        """Plan accepts abbreviation-dot-space false-splits ("U.S.A. now", "Dr. Smith")
        as a known simple-heuristic failure mode. Encode the current behavior so a
        future improvement is visible as a test change."""
        buf = SentenceBuffer()
        assert buf.feed("I live in U.S.A. now.\n") == ["I live in U.S.A.", "now."]

    def test_dr_abbreviation_splits_known_limitation(self) -> None:
        buf = SentenceBuffer()
        out = buf.feed("Dr. Smith arrived.\n")
        assert out == ["Dr.", "Smith arrived."]


class TestQuotes:
    def test_period_inside_quote_does_not_flush(self) -> None:
        buf = SentenceBuffer()
        # The period inside "hi." is suppressed; the outer period fires.
        out = buf.feed('He said "hi." then left.\n')
        assert out == ['He said "hi." then left.']

    def test_question_inside_quote_does_not_flush(self) -> None:
        buf = SentenceBuffer()
        out = buf.feed('She asked "are you sure?" quietly.\n')
        assert out == ['She asked "are you sure?" quietly.']


class TestWordThreshold:
    def test_20_word_gap_forces_flush(self) -> None:
        buf = SentenceBuffer(word_flush_threshold=20)
        # 22 words, no terminal punctuation anywhere
        text = " ".join([f"word{i}" for i in range(22)])
        out = buf.feed(text)
        assert out == [text]
        assert buf.force_flush_count == 1

    def test_no_force_flush_under_threshold(self) -> None:
        buf = SentenceBuffer(word_flush_threshold=20)
        text = " ".join(["w"] * 10)
        assert buf.feed(text) == []
        assert buf.force_flush_count == 0

    def test_threshold_resets_after_fire(self) -> None:
        buf = SentenceBuffer(word_flush_threshold=5)
        buf.feed("one two three four five six seven")  # force flush
        # Next batch starts clean
        assert buf.feed("eight") == []
        assert buf.force_flush_count == 1


class TestStreamingFeed:
    def test_delta_by_delta_behaves_like_joined(self) -> None:
        buf_stream = SentenceBuffer()
        buf_bulk = SentenceBuffer()
        deltas = ["Hel", "lo the", "re. How ", "are ", "you?\n"]
        stream_out: list[str] = []
        for d in deltas:
            stream_out.extend(buf_stream.feed(d))
        stream_out.extend(buf_stream.flush())

        bulk_out = buf_bulk.feed("".join(deltas)) + buf_bulk.flush()
        assert stream_out == bulk_out == ["Hello there.", "How are you?"]
