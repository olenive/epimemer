"""Tests for valid time — imprecise instants, validity intervals, comparison.

The thing under test is mostly *refusal*: the module answers `unknown` far more
often than it answers anything else, and every one of those refusals is a place
where an earlier design would have invented a boundary. So the assertions come
in pairs — what it concludes, and what it declines to conclude from data that
looks almost sufficient.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from epimemer.core.temporal import (
    ImpreciseInstant,
    IntervalBasis,
    NamedInstant,
    PreciseInstant,
    TemporalRelation,
    UnboundedInstant,
    UnknownInstant,
    ValidityInterval,
    ValidityVerdict,
    assertions_are_disjoint,
    compare_intervals,
    merged_validity,
    validity_at,
)

PACKAGE = Path(__file__).resolve().parents[2] / "epimemer"


def _at(year: int, month: int = 1, day: int = 1) -> PreciseInstant:
    return PreciseInstant(at=datetime(year, month, day, tzinfo=timezone.utc))


def _span(
    start: ImpreciseInstant | None = None,
    end: ImpreciseInstant | None = None,
    *,
    basis: IntervalBasis = IntervalBasis.STATED,
    **kwargs,
) -> ValidityInterval:
    return ValidityInterval(
        start=start if start is not None else UnknownInstant(),
        end=end if end is not None else UnknownInstant(),
        basis=basis,
        **kwargs,
    )


class TestEndpointsKeepUnknownAndUnboundedApart:
    """The distinction the whole type exists for.

    *"The city is named Placeberg"* has a start nobody knows; *"water is H₂O"*
    has no start. One value for both is what made `Timepoint.start: datetime |
    None` unusable here, and collapsing them reproduces the empty-set ambiguity
    one level down.
    """

    def test_the_two_are_not_the_same_value(self):
        assert UnknownInstant() != UnboundedInstant()

    def test_a_named_endpoint_keeps_the_words_the_source_used(self):
        renaissance = NamedInstant(label="during the Renaissance")

        assert renaissance.label == "during the Renaissance"

    def test_a_resolved_endpoint_keeps_the_phrase_that_justified_it(self):
        resolved = PreciseInstant(
            at=datetime(1991, 9, 6, tzinfo=timezone.utc), label="the 1991 renaming"
        )

        assert resolved.at.year == 1991
        assert resolved.label == "the 1991 renaming"

    def test_endpoints_survive_a_round_trip_by_their_discriminator(self):
        """Serialization must not quietly turn one endpoint state into another."""
        original = _span(
            start=UnboundedInstant(),
            end=NamedInstant(label="during the Renaissance"),
            witnessed_at=_at(1500),
        )

        restored = ValidityInterval.model_validate(original.model_dump())

        assert isinstance(restored.start, UnboundedInstant)
        assert isinstance(restored.end, NamedInstant)
        assert restored.end.label == "during the Renaissance"
        assert isinstance(restored.witnessed_at, PreciseInstant)


class TestAHandWrittenDateStillCompares:
    """Historical dates are typed by hand and hand-typed dates are naive.

    Comparing a naive datetime to an aware one raises, which would surface as an
    exception thrown from inside a comparison rather than as the data problem it
    is. The assumption is made once, at construction.
    """

    def test_a_naive_date_is_read_as_utc(self):
        instant = PreciseInstant(at=datetime(1970, 1, 1))

        assert instant.at.tzinfo is not None

    def test_a_naive_interval_compares_instead_of_raising(self):
        leningrad = _span(end=PreciseInstant(at=datetime(1991, 1, 1)))
        after = _span(start=PreciseInstant(at=datetime(1991, 1, 1)))

        assert compare_intervals(leningrad, after) is TemporalRelation.BEFORE


class TestAnIntervalRefusesToContradictItself:
    """A self-contradictory interval is a construction error, not a source.

    No document says *"as of 1990, Labour governed 1997–2010"*. Left unchecked,
    such an interval would also let the comparison derive an overlap from a
    premise that cannot hold. The check fires only on located positions, so an
    interval full of unknowns is always accepted.
    """

    def test_an_interval_cannot_end_before_it_starts(self):
        with pytest.raises(ValidationError, match="must start before it ends"):
            _span(start=_at(2010), end=_at(1997))

    def test_a_zero_width_interval_is_empty_and_refused(self):
        with pytest.raises(ValidationError, match="must start before it ends"):
            _span(start=_at(1997), end=_at(1997))

    def test_a_witness_before_the_stated_span_is_refused(self):
        with pytest.raises(ValidationError, match="falls outside"):
            _span(start=_at(1997), end=_at(2010), witnessed_at=_at(1990))

    def test_a_witness_on_the_end_is_outside_because_the_span_is_half_open(self):
        with pytest.raises(ValidationError, match="falls outside"):
            _span(start=_at(1997), end=_at(2010), witnessed_at=_at(2010))

    def test_a_witness_on_the_start_is_inside_for_the_same_reason(self):
        span = _span(start=_at(1997), end=_at(2010), witnessed_at=_at(1997))

        assert span.witnessed_at is not None

    def test_unknown_endpoints_never_trip_the_check(self):
        """The common shape: a source asserts a period and dates none of it."""
        span = _span(witnessed_at=_at(1970))

        assert isinstance(span.start, UnknownInstant)


class TestComparisonAnswersOnlyWhatItKnows:
    """Four values, and `unknown` is the honest majority answer.

    `unknown` is never a probability: *no information about the ordering* and
    *even odds* are different claims, and this file's whole subject is what
    happens when a model collapses them.
    """

    def test_separated_periods_are_ordered_both_ways(self):
        earlier = _span(start=_at(1924), end=_at(1991))
        later = _span(start=_at(1991), end=_at(2000))

        assert compare_intervals(earlier, later) is TemporalRelation.BEFORE
        assert compare_intervals(later, earlier) is TemporalRelation.AFTER

    def test_adjoining_periods_do_not_overlap_at_the_join(self):
        """Half-open: the instant of the rename belongs to the new name only."""
        leningrad = _span(start=_at(1924), end=_at(1991))
        saint_petersburg = _span(start=_at(1991), end=UnboundedInstant())

        assert compare_intervals(leningrad, saint_petersburg) is TemporalRelation.BEFORE

    def test_periods_sharing_a_stretch_overlap(self):
        almanac = _span(start=_at(1997), end=_at(2010))
        blog = _span(start=_at(1995), end=_at(2001))

        assert compare_intervals(almanac, blog) is TemporalRelation.OVERLAP
        assert compare_intervals(blog, almanac) is TemporalRelation.OVERLAP

    def test_an_unknown_endpoint_withholds_the_answer(self):
        """*"The city is called Leningrad"*, no end stated — it may still hold."""
        leningrad = _span(start=_at(1924))
        later = _span(start=_at(1991), end=_at(2000))

        assert compare_intervals(leningrad, later) is TemporalRelation.UNKNOWN

    def test_a_named_endpoint_compares_as_unknown_however_suggestive(self):
        """A label is what the source said; a bound is what can be computed."""
        vague = _span(start=_at(1400), end=NamedInstant(label="the Renaissance"))
        modern = _span(start=_at(1900), end=_at(2000))

        assert compare_intervals(vague, modern) is TemporalRelation.UNKNOWN

    def test_a_timeless_claim_overlaps_a_period_nobody_dated(self):
        """Every moment of any period falls inside a claim that always held."""
        water = _span(start=UnboundedInstant(), end=UnboundedInstant())
        undated = _span()

        assert compare_intervals(water, undated) is TemporalRelation.OVERLAP
        assert compare_intervals(undated, water) is TemporalRelation.OVERLAP

    def test_two_undated_periods_say_nothing_about_each_other(self):
        assert compare_intervals(_span(), _span()) is TemporalRelation.UNKNOWN

    def test_the_answer_reads_the_same_from_either_side(self):
        """`before` one way is `after` the other, for every pair, always.

        Asserted over a matrix rather than case by case because the failure it
        guards is asymmetry introduced later — a rule added to one branch and
        not its mirror — which no single example would catch.
        """
        spans = [
            _span(),
            _span(start=UnboundedInstant(), end=UnboundedInstant()),
            _span(start=_at(1924), end=_at(1991)),
            _span(start=_at(1991), end=_at(2000)),
            _span(start=_at(1980), end=_at(1995)),
            _span(start=_at(1924)),
            _span(end=_at(1991)),
            _span(end=NamedInstant(label="the Renaissance")),
            _span(witnessed_at=_at(1970)),
            _span(start=_at(1924), end=_at(1991), witnessed_at=_at(1970)),
        ]
        inverse = {
            TemporalRelation.BEFORE: TemporalRelation.AFTER,
            TemporalRelation.AFTER: TemporalRelation.BEFORE,
            TemporalRelation.OVERLAP: TemporalRelation.OVERLAP,
            TemporalRelation.UNKNOWN: TemporalRelation.UNKNOWN,
        }

        for one in spans:
            for other in spans:
                assert compare_intervals(other, one) is inverse[
                    compare_intervals(one, other)
                ], f"asymmetric answer for {one} vs {other}"


class TestWitnessPointsCarryUndatedSources:
    """Without them two undated facts never provably overlap.

    Three endpoint states cannot express *"contains 1990"* — that is a bound,
    and bounds are what T1 chose not to model — so the assertion rides on the
    interval as its own field, and the soundness check would never fire on
    undated sources without it.
    """

    def test_two_undated_facts_witnessed_at_the_same_moment_overlap(self):
        one = _span(witnessed_at=_at(1970))
        other = _span(witnessed_at=_at(1970))

        assert compare_intervals(one, other) is TemporalRelation.OVERLAP

    def test_a_witness_inside_the_other_span_overlaps(self):
        undated = _span(witnessed_at=_at(1985))
        dated = _span(start=_at(1980), end=_at(1990))

        assert compare_intervals(undated, dated) is TemporalRelation.OVERLAP
        assert compare_intervals(dated, undated) is TemporalRelation.OVERLAP

    def test_witnesses_at_different_moments_prove_nothing_alone(self):
        one = _span(witnessed_at=_at(1970))
        other = _span(witnessed_at=_at(1990))

        assert compare_intervals(one, other) is TemporalRelation.UNKNOWN

    def test_a_witness_never_produces_an_ordering(self):
        """It bounds an endpoint from the inside, which cannot show a period stops."""
        witnessed = _span(witnessed_at=_at(1970))
        later = _span(start=_at(1980), end=_at(1990))

        assert compare_intervals(witnessed, later) is TemporalRelation.UNKNOWN
        assert compare_intervals(later, witnessed) is TemporalRelation.UNKNOWN


class TestClocksDoNotConvert:
    """Cross-clock comparison is `unknown`, never disjoint.

    There is no conversion between an in-universe date and a real one, and
    answering `before` would invent one. The useful side-effect is that an
    inference drawn across a fictional fact and a real one is temporally
    uncheckable — the temporal sibling of `cross-frame`.
    """

    def test_the_default_wall_clock_compares_with_itself(self):
        earlier = _span(start=_at(1924), end=_at(1991))
        later = _span(start=_at(1991), end=_at(2000))

        assert earlier.timeline_id is None
        assert compare_intervals(earlier, later) is TemporalRelation.BEFORE

    def test_identical_periods_on_different_clocks_are_unknown(self):
        real = _span(start=_at(1924), end=_at(1991))
        in_universe = _span(start=_at(1924), end=_at(1991), timeline_id="dracula")

        assert compare_intervals(real, in_universe) is TemporalRelation.UNKNOWN
        assert compare_intervals(in_universe, real) is TemporalRelation.UNKNOWN

    def test_separated_periods_on_different_clocks_are_not_ordered(self):
        real = _span(start=_at(1924), end=_at(1991))
        in_universe = _span(start=_at(1991), end=_at(2000), timeline_id="dracula")

        assert compare_intervals(real, in_universe) is TemporalRelation.UNKNOWN


class TestEveryIntervalSaysHowItWasArrivedAt:
    """The agent is not a source, and the marking is what makes that auditable.

    A caller can filter to stated-only; without the field, "stick to the source"
    is a prompt instruction with nothing checking it.
    """

    def test_an_interval_must_declare_its_basis(self):
        with pytest.raises(ValidationError):
            ValidityInterval(start=_at(1924), end=_at(1991))

    def test_world_knowledge_has_no_value_to_be_stored_under(self):
        """Forbidden, not marked: a member for it would make it storable."""
        assert {basis.value for basis in IntervalBasis} == {"stated", "inferred"}


class TestCollapsingTwoEdgesToOneSourceKeepsBothPeriods:
    """Merging two nodes leaves one provenance edge per document.

    "Intervals survive a merge for free" is the property that put validity on
    the edge, and it only holds if the edge losing the collision hands over what
    it asserted. The field is a list precisely so one source can carry several
    disjoint periods, so there is no combination rule to invent here.
    """

    def test_the_losing_edges_periods_are_kept(self):
        first = _span(start=_at(1997), end=_at(2010))
        second = _span(start=_at(2024))

        assert merged_validity([first], [second]) == [first, second]

    def test_the_same_period_asserted_twice_is_one_assertion(self):
        """One source, one claim, one period — a repeat would later read as two."""
        span = _span(start=_at(1997), end=_at(2010))

        assert merged_validity([span], [span.model_copy(deep=True)]) == [span]

    def test_an_edge_with_nothing_to_hand_over_changes_nothing(self):
        span = _span(start=_at(1997), end=_at(2010))

        assert merged_validity([span], []) == [span]
        assert merged_validity([], [span]) == [span]

    def test_the_kept_list_is_not_mutated(self):
        """The caller rebinds; a shared list would leak into the original edge."""
        kept = [_span(start=_at(1997), end=_at(2010))]

        merged_validity(kept, [_span(start=_at(2024))])

        assert len(kept) == 1


class TestAskingWhetherAClaimHeldAtAMoment:
    """T3's retrieval buckets, and the third one that cannot exist.

    An interval says what a source *asserts* and asserts nothing about the
    outside (T1 §6), so a moment nobody dated is `unknown` — never false. That is
    why there are two verdicts rather than three, and it is what makes a
    valid-time *filter* unimplementable rather than merely misleading: there is
    no negative to filter on.
    """

    def test_a_moment_inside_a_stated_period_is_valid(self):
        assert validity_at(
            [_span(start=_at(1924), end=_at(1991))], datetime(1980, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.VALID

    def test_a_moment_outside_every_stated_period_is_unknown_not_false(self):
        """The open-world rule, and the whole reason nothing is ever excluded."""
        assert validity_at(
            [_span(start=_at(1924), end=_at(1991))], datetime(2000, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.UNKNOWN

    def test_a_claim_nobody_dated_is_unknown(self):
        assert validity_at([], datetime(1980, 1, 1, tzinfo=timezone.utc)) is (
            ValidityVerdict.UNKNOWN
        )

    def test_unknown_endpoints_conclude_nothing(self):
        assert validity_at(
            [_span()], datetime(1980, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.UNKNOWN

    def test_a_claim_asserted_to_have_always_held_covers_any_moment(self):
        always = _span(start=UnboundedInstant(), end=UnboundedInstant())

        assert validity_at(
            [always], datetime(1980, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.VALID

    def test_the_period_that_starts_on_an_instant_owns_it(self):
        """Half-open, `[start, end)`. The renaming instant belongs to the new name."""
        renamed = datetime(1991, 1, 1, tzinfo=timezone.utc)

        assert validity_at([_span(start=_at(1924), end=_at(1991))], renamed) is (
            ValidityVerdict.UNKNOWN
        )
        assert validity_at([_span(start=_at(1991), end=_at(2100))], renamed) is (
            ValidityVerdict.VALID
        )

    def test_a_started_period_with_no_known_end_concludes_nothing_alone(self):
        """The end could be anywhere, so the moment could be past it.

        Not a defect: `unbounded` is how a source says a claim can never stop,
        and reading `unknown` as though it meant that is the fabrication the two
        values exist to keep apart.
        """
        assert validity_at(
            [_span(start=_at(1991))], datetime(2010, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.UNKNOWN

    def test_a_witness_reaches_from_the_start_to_itself(self):
        """What witness points are for: concluding where an endpoint cannot.

        A source that says *"called Saint Petersburg since 1991"* as of 2020 has
        asserted the period was still running in 2020, so 2010 is provably inside
        it — and 2021 is still not.
        """
        still_running = _span(
            start=_at(1991), witnessed_at=_at(2020),
        )

        assert validity_at(
            [still_running], datetime(2010, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.VALID
        assert validity_at(
            [still_running], datetime(2021, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.UNKNOWN

    def test_a_witness_reaches_forward_to_a_known_end(self):
        """The mirror: an unknown *start* with a witness bounds from the inside."""
        span = _span(end=_at(1991), witnessed_at=_at(1950))

        assert validity_at(
            [span], datetime(1960, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.VALID
        assert validity_at(
            [span], datetime(1940, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.UNKNOWN

    def test_the_witnessed_moment_itself_is_inside(self):
        assert validity_at(
            [_span(witnessed_at=_at(1990))], datetime(1990, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.VALID

    def test_one_source_is_enough(self):
        """Existential, not universal: two sources may describe two episodes.

        Intersecting them would answer "never" for a claim both sources say was
        true — the failure T1 §3 rules out a default collapse over.
        """
        sources = [_span(start=_at(1997), end=_at(2010)), _span(start=_at(2024))]

        assert validity_at(sources, datetime(2000, 1, 1, tzinfo=timezone.utc)) is (
            ValidityVerdict.VALID
        )

    def test_a_period_on_another_clock_answers_nothing(self):
        """No conversion exists between an in-universe date and a real one."""
        in_universe = _span(start=_at(1924), end=_at(1991), timeline_id="third-age")

        assert validity_at(
            [in_universe], datetime(1980, 1, 1, tzinfo=timezone.utc)
        ) is ValidityVerdict.UNKNOWN
        assert validity_at(
            [in_universe],
            datetime(1980, 1, 1, tzinfo=timezone.utc),
            timeline_id="third-age",
        ) is ValidityVerdict.VALID

    def test_a_naive_moment_is_read_as_utc(self):
        """Rather than raising from inside a comparison, as `PreciseInstant` does."""
        assert validity_at(
            [_span(start=_at(1924), end=_at(1991))], datetime(1980, 1, 1)
        ) is ValidityVerdict.VALID

    def test_there_are_exactly_two_verdicts(self):
        """A value nothing can produce is worse than no value: callers branch on it.

        If closed-world assertions ever land, a third member lands with them —
        and this failing is the reminder to check that the producer landed too.
        """
        assert [verdict.value for verdict in ValidityVerdict] == ["valid", "unknown"]


class TestWhetherTwoClaimsWereEverBothAsserted:
    """The rule behind the soundness check (T1 §11, second pass).

    The collapse per side is the **existential union** — the moments *some*
    source asserts the claim held — and the check fires only when every cross
    pair provably falls clear. The error direction is what makes that the one
    collapse §3 permits: a sloppy, over-wide source can suppress a flag and
    never manufacture one. An implementer reaching for the intersection instead
    gets false flags, which is why the rule is pinned here rather than left to
    the call site.
    """

    def test_two_dated_claims_that_fall_clear_are_disjoint(self):
        governed = [_span(start=_at(1997), end=_at(2010))]
        later = [_span(start=_at(2024), end=_at(2030))]

        assert assertions_are_disjoint(governed, later) is True
        assert assertions_are_disjoint(later, governed) is True

    def test_overlapping_periods_are_not(self):
        assert assertions_are_disjoint(
            [_span(start=_at(1997), end=_at(2010))],
            [_span(start=_at(2005), end=_at(2030))],
        ) is False

    def test_one_source_asserting_a_second_episode_suppresses_the_finding(self):
        """The union per side, and the safe error direction it buys.

        The 2024 episode is asserted by somebody, so the two claims *were* held
        together as far as the record goes — an intersection rule would call
        that disjoint and flag a sound inference.
        """
        two_episodes = [
            _span(start=_at(1997), end=_at(2010)),
            _span(start=_at(2024), end=_at(2030)),
        ]

        assert assertions_are_disjoint(
            two_episodes, [_span(start=_at(2025), end=_at(2026))]
        ) is False

    def test_every_pair_must_fall_clear(self):
        """Two episodes either side of a period is still disjoint from it."""
        two_episodes = [
            _span(start=_at(1997), end=_at(2010)),
            _span(start=_at(2024), end=_at(2030)),
        ]

        assert assertions_are_disjoint(
            two_episodes, [_span(start=_at(2011), end=_at(2012))]
        ) is True

    def test_a_pair_that_cannot_be_placed_blocks_the_finding(self):
        """Never fires on unknown — otherwise it is a check on ignorance."""
        assert assertions_are_disjoint(
            [_span(start=_at(1997), end=_at(2010))], [_span()]
        ) is False

    def test_one_vague_period_beside_a_definite_one_still_blocks_it(self):
        """Adding an unplaceable period suppresses; it must never manufacture."""
        definite_and_vague = [_span(start=_at(1997), end=_at(2010)), _span()]

        assert assertions_are_disjoint(
            definite_and_vague, [_span(start=_at(2024), end=_at(2030))]
        ) is False

    def test_a_claim_nobody_dated_is_not_evidence(self):
        dated = [_span(start=_at(1997), end=_at(2010))]

        assert assertions_are_disjoint(dated, []) is False
        assert assertions_are_disjoint([], dated) is False
        assert assertions_are_disjoint([], []) is False

    def test_periods_on_different_clocks_never_fire(self):
        """There is no conversion between an in-universe date and a real one."""
        assert assertions_are_disjoint(
            [_span(start=_at(1997), end=_at(2010))],
            [_span(start=_at(2024), end=_at(2030), timeline_id="third-age")],
        ) is False

    def test_a_claim_asserted_to_have_always_held_is_disjoint_from_nothing(self):
        always = [_span(start=UnboundedInstant(), end=UnboundedInstant())]

        assert assertions_are_disjoint(
            always, [_span(start=_at(1997), end=_at(2010))]
        ) is False


class TestTheEndpointKindIsReadInOnePlace:
    """The extensibility rule is discipline rather than schema.

    Adding an endpoint kind — a probability distribution, if that day comes —
    must stay a small, known change. Nothing *branches* on which kind an
    endpoint is; consumers ask the comparison question and consume the answer.
    This is the structural test T1 §4 asked for, and it fails the moment a third
    file starts reading the discriminator.

    The one place besides the definition is the ingest guidance, which has to
    name the shapes an agent writes. That is documentation of the type rather
    than a branch on it, and naming it here is what keeps it from being
    forgotten when a kind is added.
    """

    def test_the_discriminator_lives_where_it_is_defined_and_documented(self):
        readers = sorted(
            path.relative_to(PACKAGE).as_posix()
            for path in PACKAGE.rglob("*.py")
            if "node_modules" not in path.parts
            and "instant_kind" in path.read_text(encoding="utf-8")
        )

        assert readers == ["core/temporal.py", "mcp/server.py"], (
            "the endpoint kind is read somewhere new, so adding a kind is no "
            f"longer the two-file change it is designed to be: {readers}"
        )
