"""Enumerating a rule, finding the nearest one, and turning one into a point.

The reasoning is all pure: a recurrence in, occurrences or a new timeline out.
What a backend is needed for is tested in `tests/mcp/test_recurrence.py`.

Two of these tests are about cost rather than about output. Nearest and next
are one division on a periodic rule and one `before`/`after` call on a calendar
one, and the obvious implementation of both is "enumerate, then sort", which
works on every small example and hangs on a daily rule anchored a century back.
So they are asserted against exactly that rule, and they fail on the clock.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from epimemer.core.types import (
    BoundChange,
    CalendarRule,
    JudgeRef,
    PeriodicRule,
    Recurrence,
    RecurrenceBounds,
    RecurrenceException,
    Timeline,
    Timepoint,
)
from epimemer.pipelines.timeline import recurrence as rec

ARCHIVIST = JudgeRef(agent_id="archivist", digest="d1")


def _dt(year: int, month: int = 1, day: int = 1, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _weekly(**kwargs) -> Recurrence:
    """A service every seven days from the first of 1890."""
    return Recurrence(
        id="weekly",
        label="the service",
        rule=PeriodicRule(anchor=_dt(1890), period=timedelta(days=7)),
        duration=timedelta(hours=1),
        **kwargs,
    )


def _daily_from_1900() -> Recurrence:
    """The rule an enumerate-then-sort implementation cannot afford."""
    return Recurrence(
        id="daily",
        label="the bell",
        rule=PeriodicRule(anchor=_dt(1900), period=timedelta(days=1)),
    )


def _monthly_calendar() -> Recurrence:
    return Recurrence(
        id="calendar",
        label="the second Tuesday",
        rule=CalendarRule(rrule="DTSTART:19000101T000000Z\nRRULE:FREQ=MONTHLY;BYDAY=2TU"),
        duration=timedelta(0),
    )


class TestEnumeration:
    def test_a_window_holds_the_occurrences_the_rule_produces(self):
        window = rec.occurrences_in_window(
            _weekly(), window_start=_dt(1890), window_end=_dt(1890, 2, 1)
        )
        assert [occurrence.start for occurrence in window.occurrences] == [
            _dt(1890, 1, 1),
            _dt(1890, 1, 8),
            _dt(1890, 1, 15),
            _dt(1890, 1, 22),
            _dt(1890, 1, 29),
        ]
        assert window.truncated is False
        assert window.occurrences[0].end == _dt(1890, 1, 1, 1)

    def test_a_calendar_rule_answers_in_the_same_shape(self):
        window = rec.occurrences_in_window(
            _monthly_calendar(), window_start=_dt(1900, 1, 1), window_end=_dt(1900, 3, 31)
        )
        assert [occurrence.start for occurrence in window.occurrences] == [
            _dt(1900, 1, 9),
            _dt(1900, 2, 13),
            _dt(1900, 3, 13),
        ]

    def test_the_cap_fires_and_the_response_says_so(self):
        window = rec.occurrences_in_window(
            _daily_from_1900(), window_start=_dt(1900), window_end=_dt(2000), cap=10
        )
        assert len(window.occurrences) == 10
        assert window.truncated is True
        # The window actually covered, which is what a caller has to ask the
        # rest of in a second call.
        assert window.covered_start == _dt(1900)
        assert window.covered_end == _dt(1900, 1, 10)

    def test_a_calendar_cap_fires_too(self):
        window = rec.occurrences_in_window(
            _monthly_calendar(), window_start=_dt(1900), window_end=_dt(2000), cap=3
        )
        assert len(window.occurrences) == 3
        assert window.truncated is True

    def test_the_default_cap_is_the_named_constant(self):
        window = rec.occurrences_in_window(
            _daily_from_1900(), window_start=_dt(1900), window_end=_dt(2000)
        )
        assert len(window.occurrences) == rec.OCCURRENCE_CAP

    def test_bounds_clip_at_both_ends(self):
        bounded = _weekly(bounds=RecurrenceBounds(start=_dt(1890, 1, 8), end=_dt(1890, 1, 22)))
        window = rec.occurrences_in_window(bounded, window_start=_dt(1889), window_end=_dt(1891))
        assert [occurrence.start for occurrence in window.occurrences] == [
            _dt(1890, 1, 8),
            _dt(1890, 1, 15),
            _dt(1890, 1, 22),
        ]

    def test_a_bound_change_ends_the_rule_where_the_latest_entry_says(self):
        ended = _weekly(
            bounds=RecurrenceBounds(start=_dt(1890)),
            bound_changes=[
                BoundChange(ends_at=_dt(1890, 1, 8), because="we thought it stopped then"),
                BoundChange(ends_at=_dt(1890, 1, 22), because="the register says 1890-01-22"),
            ],
        )
        window = rec.occurrences_in_window(ended, window_start=_dt(1889), window_end=_dt(1891))
        assert [occurrence.start for occurrence in window.occurrences][-1] == _dt(1890, 1, 22)

    def test_a_cancelled_occurrence_is_left_out(self):
        cancelled = _weekly(
            exceptions=[RecurrenceException(occurrence_start=_dt(1890, 1, 8), kind="cancelled")]
        )
        window = rec.occurrences_in_window(
            cancelled, window_start=_dt(1890), window_end=_dt(1890, 1, 20)
        )
        assert [occurrence.start for occurrence in window.occurrences] == [
            _dt(1890, 1, 1),
            _dt(1890, 1, 15),
        ]

    def test_a_cancelled_occurrence_comes_back_when_it_is_asked_for(self):
        cancelled = _weekly(
            exceptions=[RecurrenceException(occurrence_start=_dt(1890, 1, 8), kind="cancelled")]
        )
        window = rec.occurrences_in_window(
            cancelled,
            window_start=_dt(1890),
            window_end=_dt(1890, 1, 20),
            include_cancelled=True,
        )
        assert [occurrence.cancelled for occurrence in window.occurrences] == [False, True, False]

    def test_a_moved_occurrence_is_returned_at_its_new_date_with_its_old_identity(self):
        moved = _weekly(
            exceptions=[
                RecurrenceException(
                    occurrence_start=_dt(1890, 1, 8), kind="moved", moved_to=_dt(1890, 1, 10)
                )
            ]
        )
        window = rec.occurrences_in_window(
            moved, window_start=_dt(1890), window_end=_dt(1890, 1, 20)
        )
        second = window.occurrences[1]
        assert second.start == _dt(1890, 1, 10)
        assert second.occurrence_start == _dt(1890, 1, 8)
        assert second.moved is True
        assert second.end == _dt(1890, 1, 10, 1)


class TestNearestDoesNoEnumeration:
    def test_a_periodic_rule_answers_by_division(self):
        started = time.monotonic()
        found = rec.nearest_occurrence(_daily_from_1900(), _dt(2026, 9, 17, 5))
        elapsed = time.monotonic() - started
        assert found.occurrence_start == _dt(2026, 9, 17)
        assert elapsed < 0.05, "nearest enumerated the century instead of dividing"

    def test_it_can_answer_backwards_too(self):
        found = rec.nearest_occurrence(_daily_from_1900(), _dt(2026, 9, 17, 20))
        assert found.occurrence_start == _dt(2026, 9, 18)

    def test_a_calendar_rule_answers_with_before_and_after(self):
        daily = Recurrence(
            label="the bell",
            rule=CalendarRule(rrule="DTSTART:19000101T000000Z\nRRULE:FREQ=DAILY"),
        )
        started = time.monotonic()
        found = rec.nearest_occurrence(daily, _dt(2026, 9, 17, 3))
        elapsed = time.monotonic() - started
        assert found.occurrence_start == _dt(2026, 9, 17)
        assert elapsed < 1.0, "nearest walked the rule from 1900"

    def test_a_cancelled_occurrence_is_stepped_over(self):
        cancelled = _weekly(
            exceptions=[RecurrenceException(occurrence_start=_dt(1890, 1, 8), kind="cancelled")]
        )
        found = rec.nearest_occurrence(cancelled, _dt(1890, 1, 8))
        assert found.occurrence_start in (_dt(1890, 1, 1), _dt(1890, 1, 15))

    def test_bounds_keep_nearest_inside_the_rule(self):
        bounded = _weekly(bounds=RecurrenceBounds(end=_dt(1890, 1, 15)))
        found = rec.nearest_occurrence(bounded, _dt(1990))
        assert found.occurrence_start == _dt(1890, 1, 15)

    def test_a_rule_with_nothing_left_answers_nothing(self):
        spent = _weekly(bounds=RecurrenceBounds(start=_dt(1890), end=_dt(1890)))
        exhausted = spent.model_copy(
            update={
                "exceptions": [RecurrenceException(occurrence_start=_dt(1890), kind="cancelled")]
            }
        )
        assert rec.nearest_occurrence(exhausted, _dt(1890)) is None


class TestNextAfter:
    def test_next_is_strictly_after_the_moment_it_is_measured_from(self):
        found = rec.next_occurrence(_weekly(), _dt(1890, 1, 8))
        assert found.occurrence_start == _dt(1890, 1, 15)

    def test_next_answers_in_a_fictional_timelines_era(self):
        timeline = Timeline(name="Dracula", reference_time=_dt(1897, 5, 1))
        resolved = rec.resolve_reference_time(timeline, now=_dt(2026, 9, 17))
        assert resolved == _dt(1897, 5, 1)
        assert rec.next_occurrence(_weekly(), resolved).occurrence_start == _dt(1897, 5, 5)

    def test_an_unset_reference_time_follows_the_clock_it_is_handed(self):
        timeline = Timeline(name="real")
        assert rec.resolve_reference_time(timeline, now=_dt(2026, 9, 17)) == _dt(2026, 9, 17)

    def test_next_past_the_end_of_a_rule_is_nothing(self):
        bounded = _weekly(bounds=RecurrenceBounds(end=_dt(1890, 1, 15)))
        assert rec.next_occurrence(bounded, _dt(1890, 2, 1)) is None


class TestMaterialisation:
    def _timeline(self, recurrence: Recurrence) -> Timeline:
        return Timeline(id="tl", name="the parish", recurrences=[recurrence])

    def test_the_same_occurrence_twice_gives_one_point(self):
        timeline = self._timeline(_weekly())
        timeline, first = rec.materialise(
            timeline, recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        timeline, second = rec.materialise(
            timeline, recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        assert first.created is True
        assert second.created is False
        assert first.timepoint_id == second.timepoint_id
        assert len(timeline.timepoints) == 1

    def test_a_materialised_occurrence_is_an_ordinary_dated_point(self):
        timeline, report = rec.materialise(
            self._timeline(_weekly()), recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        point = timeline.timepoints[0]
        assert point.id == report.timepoint_id
        assert point.kind == "interval"
        assert point.start == _dt(1890, 1, 8)
        assert point.end == _dt(1890, 1, 8, 1)
        assert point.label == "the service"
        assert point.recurrence_id == "weekly"
        assert point.occurrence_start == _dt(1890, 1, 8)

    def test_a_zero_duration_occurrence_is_an_instant(self):
        timeline, _ = rec.materialise(
            self._timeline(_monthly_calendar()),
            recurrence_id="calendar",
            occurrence_start=_dt(1900, 1, 9),
        )
        assert timeline.timepoints[0].kind == "instant"

    def test_a_start_the_rule_does_not_produce_is_refused(self):
        timeline, report = rec.materialise(
            self._timeline(_weekly()), recurrence_id="weekly", occurrence_start=_dt(1890, 1, 9)
        )
        assert report.timepoint_id is None
        assert "does not produce" in report.refused
        assert timeline.timepoints == []

    def test_a_start_outside_the_bounds_is_refused(self):
        bounded = _weekly(bounds=RecurrenceBounds(end=_dt(1890, 1, 15)))
        _, report = rec.materialise(
            self._timeline(bounded), recurrence_id="weekly", occurrence_start=_dt(1890, 1, 22)
        )
        assert "does not produce" in report.refused

    def test_a_cancelled_occurrence_is_refused(self):
        cancelled = _weekly(
            exceptions=[RecurrenceException(occurrence_start=_dt(1890, 1, 8), kind="cancelled")]
        )
        _, report = rec.materialise(
            self._timeline(cancelled), recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        assert "cancelled" in report.refused

    def test_a_moved_occurrence_materialises_at_its_new_date_under_its_old_identity(self):
        moved = _weekly(
            exceptions=[
                RecurrenceException(
                    occurrence_start=_dt(1890, 1, 8), kind="moved", moved_to=_dt(1890, 1, 10)
                )
            ]
        )
        timeline, report = rec.materialise(
            self._timeline(moved), recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        point = timeline.timepoints[0]
        assert point.start == _dt(1890, 1, 10)
        assert point.occurrence_start == _dt(1890, 1, 8)

        # Still found by the identity the rule gave it, which is what stops a
        # move from producing a second point for one occurrence.
        _, again = rec.materialise(
            timeline, recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        assert again.created is False
        assert again.timepoint_id == report.timepoint_id

    def test_a_rule_the_timeline_does_not_have_is_refused(self):
        _, report = rec.materialise(
            self._timeline(_weekly()), recurrence_id="nope", occurrence_start=_dt(1890)
        )
        assert "no recurrence" in report.refused

    def test_a_materialised_point_keeps_the_record_in_date_order(self):
        timeline = self._timeline(_weekly())
        timeline, _ = rec.materialise(
            timeline, recurrence_id="weekly", occurrence_start=_dt(1890, 1, 15)
        )
        timeline, _ = rec.materialise(
            timeline, recurrence_id="weekly", occurrence_start=_dt(1890, 1, 1)
        )
        assert [point.start for point in timeline.timepoints] == [_dt(1890, 1, 1), _dt(1890, 1, 15)]

    def test_an_enumerated_occurrence_names_the_point_that_materialised_it(self):
        timeline = self._timeline(_weekly())
        timeline, report = rec.materialise(
            timeline, recurrence_id="weekly", occurrence_start=_dt(1890, 1, 8)
        )
        window = rec.occurrences_in_window(
            timeline.recurrences[0], window_start=_dt(1890), window_end=_dt(1890, 1, 20)
        )
        materialised = rec.materialised_ids(timeline, "weekly")
        assert materialised[window.occurrences[1].occurrence_start] == report.timepoint_id


class TestWritingRules:
    def test_a_rule_is_added_with_its_provenance(self):
        timeline, recurrence = rec.add_recurrence(
            Timeline(name="the parish"),
            label="the service",
            rule=PeriodicRule(anchor=_dt(1890), period=timedelta(days=7)),
            duration=timedelta(hours=1),
            bounds=RecurrenceBounds(start=_dt(1890)),
            source_id="doc-1",
            judge=ARCHIVIST,
            at=_dt(2026, 9, 17),
        )
        assert timeline.recurrences == [recurrence]
        assert recurrence.source_id == "doc-1"
        assert recurrence.judged_by.agent_id == "archivist"
        assert recurrence.asserted_at == _dt(2026, 9, 17)

    def test_a_mistyped_rrule_is_refused_when_the_rule_is_built(self):
        with pytest.raises(ValueError, match="recurrence rule"):
            CalendarRule(rrule="FREQ=EVERY-OTHER-TUESDAY")

    def test_two_bound_changes_read_as_a_history(self):
        timeline = Timeline(name="the parish", recurrences=[_weekly()])
        timeline, first = rec.end_recurrence(
            timeline,
            recurrence_id="weekly",
            ends_at=_dt(1990),
            because="the register stops in 1990",
            judge=ARCHIVIST,
            at=_dt(2026, 9, 17),
        )
        timeline, second = rec.end_recurrence(
            timeline,
            recurrence_id="weekly",
            ends_at=_dt(1993),
            because="a later account has it running to 1993",
            judge=ARCHIVIST,
            at=_dt(2026, 9, 18),
        )
        assert first.effective_end == _dt(1990)
        assert second.effective_end == _dt(1993)
        assert [change.ends_at for change in second.bound_changes] == [_dt(1990), _dt(1993)]
        assert [change.because for change in second.bound_changes] == [
            "the register stops in 1990",
            "a later account has it running to 1993",
        ]

    def test_ending_a_rule_never_retires_it(self):
        timeline = Timeline(name="the parish", recurrences=[_weekly()])
        timeline, _ = rec.end_recurrence(
            timeline,
            recurrence_id="weekly",
            ends_at=_dt(1890, 1, 15),
            because="the register stops there",
            at=_dt(2026, 9, 17),
        )
        window = rec.occurrences_in_window(
            timeline.recurrences[0], window_start=_dt(1890), window_end=_dt(1891)
        )
        assert len(timeline.recurrences) == 1
        assert [occurrence.start for occurrence in window.occurrences] == [
            _dt(1890, 1, 1),
            _dt(1890, 1, 8),
            _dt(1890, 1, 15),
        ]

    def test_an_exception_names_the_occurrence_the_rule_produces(self):
        timeline = Timeline(name="the parish", recurrences=[_weekly()])
        timeline, report = rec.record_exception(
            timeline,
            recurrence_id="weekly",
            occurrence_start=_dt(1890, 1, 8),
            kind="cancelled",
            source_id="doc-1",
            because="the 1890 service was called off",
            judge=ARCHIVIST,
            at=_dt(2026, 9, 17),
        )
        assert report.refused is None
        assert timeline.recurrences[0].exceptions[0].kind == "cancelled"
        assert timeline.recurrences[0].exceptions[0].judged_by.agent_id == "archivist"

    def test_an_occurrence_the_rule_does_not_produce_cannot_be_excepted(self):
        timeline = Timeline(name="the parish", recurrences=[_weekly()])
        _, report = rec.record_exception(
            timeline,
            recurrence_id="weekly",
            occurrence_start=_dt(1890, 1, 9),
            kind="cancelled",
            at=_dt(2026, 9, 17),
        )
        assert "does not produce" in report.refused

    def test_a_move_with_nowhere_to_go_is_refused(self):
        timeline = Timeline(name="the parish", recurrences=[_weekly()])
        _, report = rec.record_exception(
            timeline,
            recurrence_id="weekly",
            occurrence_start=_dt(1890, 1, 8),
            kind="moved",
            at=_dt(2026, 9, 17),
        )
        assert "moved_to" in report.refused

    def test_a_second_exception_on_one_occurrence_replaces_the_first(self):
        timeline = Timeline(name="the parish", recurrences=[_weekly()])
        timeline, _ = rec.record_exception(
            timeline,
            recurrence_id="weekly",
            occurrence_start=_dt(1890, 1, 8),
            kind="cancelled",
            at=_dt(2026, 9, 17),
        )
        timeline, _ = rec.record_exception(
            timeline,
            recurrence_id="weekly",
            occurrence_start=_dt(1890, 1, 8),
            kind="moved",
            moved_to=_dt(1890, 1, 10),
            at=_dt(2026, 9, 18),
        )
        assert [exc.kind for exc in timeline.recurrences[0].exceptions] == ["moved"]
        window = rec.occurrences_in_window(
            timeline.recurrences[0], window_start=_dt(1890), window_end=_dt(1890, 1, 20)
        )
        assert window.occurrences[1].start == _dt(1890, 1, 10)


class TestTheRecordItDoesNotTouch:
    def test_a_timeline_written_before_recurrence_existed_still_loads(self):
        older = Timeline(name="written earlier").model_dump(mode="json")
        older.pop("recurrences")
        assert Timeline.model_validate(older).recurrences == []

    def test_an_old_timepoint_reads_back_with_no_occurrence(self):
        point = Timepoint(start=_dt(1890), label="the fire").model_dump(mode="json")
        point.pop("recurrence_id")
        point.pop("occurrence_start")
        assert Timepoint.model_validate(point).recurrence_id is None
