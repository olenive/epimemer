"""Tests for timeline functional interface."""

from datetime import UTC, datetime

from epimemer.core.types import Timeline, Timepoint
from epimemer.pipelines.timeline.functions import (
    add_timepoint,
    find_nearest,
    get_in_range,
    get_timepoint,
    remove_timepoint,
    reorder_timepoints,
)


def _dt(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


class TestAddTimepoint:
    def test_inserts_concrete_in_sorted_position(self):
        tl = Timeline(name="test")
        tl, tp1 = add_timepoint(tl, start=_dt(2024, 6), label="June")
        tl, tp2 = add_timepoint(tl, start=_dt(2024, 1), label="January")
        tl, tp3 = add_timepoint(tl, start=_dt(2024, 3), label="March")

        labels = [tp.label for tp in tl.timepoints]
        assert labels == ["January", "March", "June"]

    def test_vague_timepoint_appends(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2024, 1), label="January")
        tl, _ = add_timepoint(tl, label="during the Renaissance")
        tl, _ = add_timepoint(tl, start=_dt(2024, 6), label="June")

        labels = [tp.label for tp in tl.timepoints]
        assert labels == ["January", "June", "during the Renaissance"]

    def test_returns_new_timepoint_with_id(self):
        tl = Timeline(name="test")
        tl, tp = add_timepoint(tl, start=_dt(2024, 1), label="New Year")
        assert tp.id
        assert tp.label == "New Year"
        assert tp.start == _dt(2024, 1)

    def test_does_not_mutate_original(self):
        tl1 = Timeline(name="test")
        tl2, _ = add_timepoint(tl1, start=_dt(2024, 1), label="X")
        assert len(tl1.timepoints) == 0
        assert len(tl2.timepoints) == 1


class TestRemoveTimepoint:
    def test_removes_by_id(self):
        tl = Timeline(name="test")
        tl, tp = add_timepoint(tl, start=_dt(2024, 1), label="Jan")
        tl, _ = add_timepoint(tl, start=_dt(2024, 6), label="Jun")
        assert len(tl.timepoints) == 2

        tl = remove_timepoint(tl, tp.id)
        assert len(tl.timepoints) == 1
        assert tl.timepoints[0].label == "Jun"

    def test_invalid_id_returns_unchanged(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, label="X")
        tl2 = remove_timepoint(tl, "nonexistent")
        assert len(tl2.timepoints) == 1


class TestGetTimepoint:
    def test_returns_correct_timepoint(self):
        tl = Timeline(name="test")
        tl, tp = add_timepoint(tl, start=_dt(2024, 1), label="Jan")
        tl, _ = add_timepoint(tl, start=_dt(2024, 6), label="Jun")

        found = get_timepoint(tl, tp.id)
        assert found is not None
        assert found.label == "Jan"

    def test_returns_none_for_missing(self):
        tl = Timeline(name="test")
        assert get_timepoint(tl, "nonexistent") is None


class TestFindNearest:
    def test_returns_closest_timepoints(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2020), label="2020")
        tl, _ = add_timepoint(tl, start=_dt(2022), label="2022")
        tl, _ = add_timepoint(tl, start=_dt(2024), label="2024")
        tl, _ = add_timepoint(tl, start=_dt(2026), label="2026")

        nearest = find_nearest(tl, _dt(2023), k=2)
        labels = [tp.label for tp in nearest]
        assert "2022" in labels
        assert "2024" in labels

    def test_fewer_than_k_returns_all(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2024), label="2024")

        nearest = find_nearest(tl, _dt(2023), k=5)
        assert len(nearest) == 1

    def test_empty_timeline_returns_empty(self):
        tl = Timeline(name="test")
        assert find_nearest(tl, _dt(2024)) == []

    def test_ignores_vague_timepoints(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, label="vague event")
        tl, _ = add_timepoint(tl, start=_dt(2024), label="2024")

        nearest = find_nearest(tl, _dt(2024), k=5)
        assert len(nearest) == 1
        assert nearest[0].label == "2024"


class TestGetInRange:
    def test_returns_timepoints_in_range(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2020), label="2020")
        tl, _ = add_timepoint(tl, start=_dt(2022), label="2022")
        tl, _ = add_timepoint(tl, start=_dt(2024), label="2024")
        tl, _ = add_timepoint(tl, start=_dt(2026), label="2026")

        results = get_in_range(tl, _dt(2021), _dt(2025))
        labels = {tp.label for tp in results}
        assert labels == {"2022", "2024"}

    def test_no_matches_returns_empty(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2020), label="2020")

        results = get_in_range(tl, _dt(2022), _dt(2025))
        assert results == []

    def test_interval_overlap(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2020), end=_dt(2025), label="2020-2025")

        # Range overlaps with the interval
        results = get_in_range(tl, _dt(2023), _dt(2027))
        assert len(results) == 1
        assert results[0].label == "2020-2025"

    def test_interval_before_range_extending_into_it(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2018), end=_dt(2023), label="2018-2023")

        results = get_in_range(tl, _dt(2022), _dt(2025))
        assert len(results) == 1

    def test_results_are_in_chronological_order(self):
        """A straddling interval started first, so it is reported first. A
        caller reading the list as a chronology must not have to re-sort it."""
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2018), end=_dt(2023), label="the long war")
        tl, _ = add_timepoint(tl, start=_dt(2022), label="the treaty")
        tl, _ = add_timepoint(tl, start=_dt(2024), label="the election")

        results = get_in_range(tl, _dt(2021), _dt(2025))
        assert [tp.label for tp in results] == ["the long war", "the treaty", "the election"]

    def test_several_straddling_intervals_keep_their_order(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2000), end=_dt(2030), label="the century")
        tl, _ = add_timepoint(tl, start=_dt(2010), end=_dt(2022), label="the decade")
        tl, _ = add_timepoint(tl, start=_dt(2021), label="the year")

        results = get_in_range(tl, _dt(2020), _dt(2025))
        assert [tp.label for tp in results] == ["the century", "the decade", "the year"]

    def test_an_interval_that_ended_before_the_window_is_left_out(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2000), end=_dt(2005), label="over already")
        tl, _ = add_timepoint(tl, start=_dt(2021), label="the year")

        results = get_in_range(tl, _dt(2020), _dt(2025))
        assert [tp.label for tp in results] == ["the year"]

    def test_an_interval_touching_the_window_edge_is_included(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, start=_dt(2010), end=_dt(2020), label="up to the line")

        assert [tp.label for tp in get_in_range(tl, _dt(2020), _dt(2025))] == ["up to the line"]

    def test_vague_points_are_left_out(self):
        tl = Timeline(name="test")
        tl, _ = add_timepoint(tl, label="during the Renaissance")
        tl, _ = add_timepoint(tl, start=_dt(2021), label="the year")

        assert [tp.label for tp in get_in_range(tl, _dt(2020), _dt(2025))] == ["the year"]

    def test_points_out_of_order_in_the_record_still_come_back_in_order(self):
        """`get_in_range` promises a chronology, so it does not depend on the
        record happening to be sorted."""
        tl = Timeline(
            name="test",
            timepoints=[
                Timepoint(start=_dt(2024), label="later"),
                Timepoint(start=_dt(2021), label="earlier"),
            ],
        )

        results = get_in_range(tl, _dt(2020), _dt(2025))
        assert [tp.label for tp in results] == ["earlier", "later"]

    def test_a_long_history_before_the_window_is_skipped_correctly(self):
        """The backwards walk is bounded by the longest interval on the
        timeline, so a century of short intervals before the window is passed
        over without changing the answer."""
        tl = Timeline(name="test")
        for year in range(1800, 1900):
            tl, _ = add_timepoint(tl, start=_dt(year), end=_dt(year + 1), label=str(year))
        tl, _ = add_timepoint(tl, start=_dt(1850), end=_dt(1995), label="the long one")
        tl, _ = add_timepoint(tl, start=_dt(1990), label="the year")

        results = get_in_range(tl, _dt(1990), _dt(1991))
        assert [tp.label for tp in results] == ["the long one", "the year"]


class TestReorderTimepoints:
    def test_sorts_by_start_datetime(self):
        tl = Timeline(
            name="test",
            timepoints=[
                Timepoint(start=_dt(2024), label="2024"),
                Timepoint(start=_dt(2020), label="2020"),
                Timepoint(start=_dt(2022), label="2022"),
            ],
        )
        tl = reorder_timepoints(tl)
        labels = [tp.label for tp in tl.timepoints]
        assert labels == ["2020", "2022", "2024"]

    def test_vague_at_end(self):
        tl = Timeline(
            name="test",
            timepoints=[
                Timepoint(label="vague"),
                Timepoint(start=_dt(2024), label="2024"),
                Timepoint(start=_dt(2020), label="2020"),
            ],
        )
        tl = reorder_timepoints(tl)
        labels = [tp.label for tp in tl.timepoints]
        assert labels == ["2020", "2024", "vague"]


class TestStableUUIDs:
    def test_uuids_survive_add_remove(self):
        tl = Timeline(name="test")
        tl, tp1 = add_timepoint(tl, start=_dt(2020), label="2020")
        tl, tp2 = add_timepoint(tl, start=_dt(2024), label="2024")
        original_id2 = tp2.id

        # Remove and check the remaining UUID is stable
        tl = remove_timepoint(tl, tp1.id)
        assert tl.timepoints[0].id == original_id2

    def test_uuids_survive_reorder(self):
        tp1 = Timepoint(start=_dt(2024), label="2024")
        tp2 = Timepoint(start=_dt(2020), label="2020")
        tl = Timeline(name="test", timepoints=[tp1, tp2])

        tl = reorder_timepoints(tl)
        # After reorder, IDs should be preserved
        ids = {tp.id for tp in tl.timepoints}
        assert tp1.id in ids
        assert tp2.id in ids
