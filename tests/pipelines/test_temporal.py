"""Temporal expression detection.

The rule that matters most here is the one about what *not* to do: an
unresolvable expression must stay unresolved. The timeline panel has an undated
lane precisely so that "during the Renaissance" can be shown without anyone
inventing 1500-01-01 for it, and a wrong date is far more expensive than a
missing one — it is indistinguishable from a real one once stored.
"""

from datetime import UTC, datetime

import pytest

from epimemer.pipelines.timeline.temporal import (
    TemporalExpression,
    detect_temporal_expressions,
)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def only(text: str) -> TemporalExpression:
    found = detect_temporal_expressions(text)
    assert len(found) == 1, f"expected one expression in {text!r}, got {found}"
    return found[0]


class TestConcreteDates:
    """A resolved expression carries a half-open interval: `start` is the first
    instant it covers, `end` the first instant after it. A day, a month and a
    year are all intervals — only their width differs."""

    @pytest.mark.parametrize(
        "text,start,end",
        [
            ("on 2024-06-01 the vote passed", utc(2024, 6, 1), utc(2024, 6, 2)),
            ("in 2024-06 the vote passed", utc(2024, 6, 1), utc(2024, 7, 1)),
            ("on 12 March 1997 he resigned", utc(1997, 3, 12), utc(1997, 3, 13)),
            ("on March 12, 1997 he resigned", utc(1997, 3, 12), utc(1997, 3, 13)),
            ("on 12th March 1997 he resigned", utc(1997, 3, 12), utc(1997, 3, 13)),
            ("in March 1997 he resigned", utc(1997, 3, 1), utc(1997, 4, 1)),
            ("in 1897 the siege began", utc(1897, 1, 1), utc(1898, 1, 1)),
            ("since 1897 the siege had held", utc(1897, 1, 1), utc(1898, 1, 1)),
        ],
    )
    def test_resolves_to_an_interval(self, text, start, end):
        found = only(text)
        assert (found.start, found.end) == (start, end)
        assert found.label is None

    def test_december_rolls_into_the_next_year(self):
        found = only("in December 1999 the deal closed")
        assert (found.start, found.end) == (utc(1999, 12, 1), utc(2000, 1, 1))

    @pytest.mark.parametrize(
        "text,start,end",
        [
            ("the 1990s were quiet", utc(1990, 1, 1), utc(2000, 1, 1)),
            ("during the 1890s", utc(1890, 1, 1), utc(1900, 1, 1)),
            ("in the 19th century", utc(1801, 1, 1), utc(1901, 1, 1)),
            ("in the 20th century", utc(1901, 1, 1), utc(2001, 1, 1)),
        ],
    )
    def test_resolves_spans(self, text, start, end):
        """A decade or a century is a wide interval, not a vague one. Saying
        "the 1990s" states its own bounds; nothing is being guessed."""
        found = only(text)
        assert (found.start, found.end) == (start, end)

    @pytest.mark.parametrize(
        "text",
        [
            "the treaty was signed on 28 June 1919.",
            "the treaty was signed in 1919.",
            "the treaty was signed in 1919, at last",
            "the treaty was signed in 1919; at last",
            "the treaty was signed in 1919)",
        ],
    )
    def test_a_year_may_end_the_sentence(self, text):
        """Found by running the detector over ordinary prose: the guard against
        "1897.5" was rejecting every year followed by a full stop, which is
        where years most often sit."""
        assert detect_temporal_expressions(text) != []

    def test_keeps_the_surface_form(self):
        """The matched text is what a reader recognises, so it survives as the
        label a mark can be titled with."""
        assert only("on 12 March 1997 he resigned").text == "12 March 1997"


class TestRanges:
    @pytest.mark.parametrize(
        "text",
        [
            "from 1897 to 1901 the siege held",
            "between 1897 and 1901 the siege held",
            "the siege of 1897–1901",
        ],
    )
    def test_spans_both_endpoints(self, text):
        found = only(text)
        assert (found.start, found.end) == (utc(1897, 1, 1), utc(1902, 1, 1))

    def test_a_backwards_range_is_not_a_range(self):
        """ "From 1901 to 1897" is a typo, or a sentence that happens to contain
        two years. Either way, silently swapping the ends would be a guess — so
        it falls back to whichever endpoints stand on their own."""
        found = detect_temporal_expressions("from 1901 to 1897")
        assert [(f.start, f.end) for f in found] == [(utc(1901, 1, 1), utc(1902, 1, 1))]


class TestVagueExpressions:
    @pytest.mark.parametrize(
        "text,label",
        [
            ("during the Renaissance trade grew", "during the Renaissance"),
            ("in the Middle Ages trade grew", "in the Middle Ages"),
            ("this was decided recently", "recently"),
            ("it happened long ago", "long ago"),
            ("centuries ago the river moved", "centuries ago"),
            ("later that year he returned", "later that year"),
        ],
    )
    def test_stays_unresolved(self, text, label):
        found = only(text)
        assert found.start is None
        assert found.end is None
        assert found.label == label

    @pytest.mark.parametrize(
        "text,label",
        [
            ("throughout the Victorian Era the mill ran", "throughout the Victorian Era"),
            ("during the Cold War it was closed", "during the Cold War"),
            ("in the Bronze Age the river moved", "in the Bronze Age"),
            ("since the Enlightenment it has", "since the Enlightenment"),
            ("after the French Revolution it was sold", "after the French Revolution"),
            ("before the Second World War it was a mill", "before the Second World War"),
            ("during the Tang Dynasty it was built", "during the Tang Dynasty"),
            ("in the Age of Enlightenment it was built", "in the Age of Enlightenment"),
            ("during the War of the Roses it burned", "during the War of the Roses"),
            ("in the Sixties it was a club", "in the Sixties"),
        ],
    )
    def test_a_named_period_is_recognised_by_its_head_noun(self, text, label):
        """A capital letter marks a name, and a name is a time only when the
        noun it is built on names a stretch of time: the last word, or the one
        before "of"."""
        found = only(text)
        assert (found.start, found.label) == (None, label)


class TestWhatItRefusesToMatch:
    """Every entry here is a false positive that would put an invented date on
    the timeline. A missed expression costs a mark; an invented one is a lie the
    graph cannot tell apart from evidence."""

    @pytest.mark.parametrize(
        "text",
        [
            "the army numbered 3000 troops",
            "the file was 2048 bytes",
            "error code 1997 was returned",
            "he ran 1500 metres",
            "port 8080 was open",
            "version 2024 shipped",
            "the room was quiet",
            "in the room the light failed",
            "id 19970312 was assigned",
            "the ratio was 1897.5",
            "in 2024-13 nothing happened",
            "on 2024-02-31 nothing happened",
        ],
    )
    def test_finds_nothing(self, text):
        assert detect_temporal_expressions(text) == []

    @pytest.mark.parametrize(
        "text",
        [
            "the fix is described in the README",
            "see the note in the CHANGELOG",
            "the target is configured in the Makefile",
            "it runs on the MCP server",
            "the runbook was written by the Platform Team",
            "imported from the Python standard library",
            "the case was settled in the Supreme Court",
            "the failure was logged during the CI run",
            "the office is in the Department of War",
            "a stall in the Renaissance Fair tent",
            "the bug is in the code",
            "he stood at the window",
            "the key is in the lock",
        ],
    )
    def test_a_prepositional_phrase_naming_no_time_is_not_a_timepoint(self, text):
        """A temporal preposition followed by a capitalised name reads like
        "during the Renaissance" and usually names a file, a team or a court.
        Filed as a timepoint it lands in the undated tray as though a source
        had dated something."""
        assert detect_temporal_expressions(text) == []

    def test_a_year_needs_a_temporal_cue(self):
        """A bare four-digit number is only a year when something says so — a
        preposition, or a date pattern around it."""
        assert detect_temporal_expressions("1897 units were sold") == []
        assert detect_temporal_expressions("sold in 1897") != []

    @pytest.mark.parametrize("text", ["in 0999", "in 3200"])
    def test_ignores_years_outside_the_plausible_range(self, text):
        assert detect_temporal_expressions(text) == []


class TestTheTighteningKeepsGenuineExpressions:
    """The expressions the vague rule must not take down with it."""

    def test_a_month_and_year_still_resolves(self):
        found = only("the release shipped in March 2026")
        assert (found.start, found.end) == (utc(2026, 3, 1), utc(2026, 4, 1))

    def test_a_bare_year_after_a_preposition_still_resolves(self):
        found = only("the project was founded in 2024")
        assert (found.start, found.end) == (utc(2024, 1, 1), utc(2025, 1, 1))

    def test_a_named_date_beside_a_file_name_is_the_only_mark(self):
        found = detect_temporal_expressions("on 12 March 1997 it was noted in the README")
        assert [f.text for f in found] == ["12 March 1997"]


class TestMultipleAndOverlapping:
    def test_reports_each_expression_in_order(self):
        found = detect_temporal_expressions("on 12 March 1997 he resigned; in 1999 he returned")
        assert [f.text for f in found] == ["12 March 1997", "1999"]

    def test_a_longer_match_wins_over_the_one_inside_it(self):
        """ "12 March 1997" must not also yield "March 1997" and "1997"."""
        found = detect_temporal_expressions("on 12 March 1997")
        assert [f.text for f in found] == ["12 March 1997"]

    def test_orders_by_position_not_by_which_pattern_matched(self):
        """Patterns are tried most-specific first, so the order they match in
        is not the order they appear in."""
        found = detect_temporal_expressions("in 1999 he returned; on 12 March 1997 he left")
        assert [f.text for f in found] == ["1999", "12 March 1997"]

    def test_finds_every_year_in_a_sentence(self):
        found = detect_temporal_expressions("sales rose in 2023 and again in 2024.")
        assert [f.text for f in found] == ["2023", "2024"]

    def test_repeats_collapse_to_one(self):
        """Two mentions of the same instant are one point in time, and the
        caller turns each expression into a timepoint."""
        found = detect_temporal_expressions("in 1897, and again in 1897")
        assert len(found) == 1

    def test_an_empty_string_is_not_an_error(self):
        assert detect_temporal_expressions("") == []
