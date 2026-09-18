"""The tools that record what recurs, and read its occurrences back.

The reasoning is tested over pure functions in
`tests/pipelines/test_recurrence.py`. What is under test here is the part a
backend is needed for: the record written back, the journal row, materialising
an occurrence through `add_timepoint`, and what a reader sees on
`query_timeline`.

Both backends via the `storage` fixture.
"""

from datetime import UTC, datetime, timedelta

import pytest

from epimemer.core.temporal import IntervalBasis, PreciseInstant, ValidityInterval
from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    NodeEdge,
    NodeStatus,
    Timeline,
    Timepoint,
    edge_is_live,
)
from epimemer.mcp import tools
from epimemer.pipelines.timeline.ordering import LinkMove
from epimemer.visualization.snapshot import assemble_snapshot

ARCHIVIST = JudgeRef(agent_id="archivist", digest="d1")


def _dt(year: int, month: int = 1, day: int = 1, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


async def _timeline(storage, *, reference_time: datetime | None = None) -> Timeline:
    timeline = Timeline(name="the parish", reference_time=reference_time)
    await storage.store_timeline(timeline)
    return timeline


async def _weekly(storage, timeline_id: str) -> str:
    """A service every seven days from the first of 1890, lasting an hour."""
    result, _ = await tools.add_recurrence(
        timeline_id,
        storage,
        label="the service",
        duration=timedelta(hours=1),
        anchor=_dt(1890),
        period=timedelta(days=7),
        source_id="doc-1",
        judge=ARCHIVIST,
    )
    return result["recurrence_id"]


class TestAddRecurrence:
    async def test_the_rule_is_written_to_the_record_with_its_judge(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)

        stored = await storage.get_timeline(timeline.id)
        assert [recurrence.id for recurrence in stored.recurrences] == [recurrence_id]
        assert stored.recurrences[0].judged_by.agent_id == "archivist"
        assert stored.recurrences[0].source_id == "doc-1"
        assert stored.recurrences[0].rule.period == timedelta(days=7)

    async def test_the_response_previews_the_first_few_occurrences(self, storage):
        timeline = await _timeline(storage)
        result, _ = await tools.add_recurrence(
            timeline.id,
            storage,
            label="the service",
            anchor=_dt(1890),
            period=timedelta(days=7),
            judge=ARCHIVIST,
        )
        assert [occurrence["start"] for occurrence in result["preview"]] == [
            _dt(1890, 1, 1).isoformat(),
            _dt(1890, 1, 8).isoformat(),
            _dt(1890, 1, 15).isoformat(),
            _dt(1890, 1, 22).isoformat(),
            _dt(1890, 1, 29).isoformat(),
        ]

    async def test_a_calendar_rule_is_previewed_too(self, storage):
        timeline = await _timeline(storage)
        result, _ = await tools.add_recurrence(
            timeline.id,
            storage,
            label="the second Tuesday",
            rrule="DTSTART:19000101T000000Z\nRRULE:FREQ=MONTHLY;BYDAY=2TU",
            judge=ARCHIVIST,
        )
        assert result["added"] is True
        assert result["preview"][0]["start"] == _dt(1900, 1, 9).isoformat()

    async def test_a_mistyped_rrule_is_refused_rather_than_stored(self, storage):
        timeline = await _timeline(storage)
        result, _ = await tools.add_recurrence(
            timeline.id,
            storage,
            label="the second Tuesday",
            rrule="FREQ=EVERY-OTHER-TUESDAY",
            judge=ARCHIVIST,
        )
        assert result["added"] is False
        assert "recurrence rule" in result["refused"]

        stored = await storage.get_timeline(timeline.id)
        assert stored.recurrences == []

    async def test_two_kinds_of_rule_in_one_call_are_refused(self, storage):
        timeline = await _timeline(storage)
        result, _ = await tools.add_recurrence(
            timeline.id,
            storage,
            label="the service",
            anchor=_dt(1890),
            period=timedelta(days=7),
            rrule="DTSTART:19000101T000000Z\nRRULE:FREQ=DAILY",
            judge=ARCHIVIST,
        )
        assert result["added"] is False
        assert "not both" in result["refused"]

    async def test_a_rule_with_no_rule_in_it_is_refused(self, storage):
        timeline = await _timeline(storage)
        result, _ = await tools.add_recurrence(
            timeline.id, storage, label="the service", judge=ARCHIVIST
        )
        assert result["added"] is False
        assert "needs a rule" in result["refused"]

    async def test_one_call_writes_one_journal_row(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)

        rows = await storage.query_decisions(kinds=[DecisionKind.RECURRENCE])
        assert len(rows) == 1
        assert rows[0].judged_by.agent_id == "archivist"


class TestEndRecurrence:
    async def test_two_corrections_read_as_a_history(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)

        await tools.end_recurrence(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            ends_at=_dt(1990),
            because="the register stops in 1990",
            judge=ARCHIVIST,
        )
        result, _ = await tools.end_recurrence(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            ends_at=_dt(1993),
            because="a later account has it running to 1993",
            judge=ARCHIVIST,
        )
        assert datetime.fromisoformat(result["effective_end"]) == _dt(1993)
        assert [
            datetime.fromisoformat(change["ends_at"]) for change in result["bound_changes"]
        ] == [_dt(1990), _dt(1993)]

        stored = await storage.get_timeline(timeline.id)
        assert len(stored.recurrences) == 1
        assert [change.because for change in stored.recurrences[0].bound_changes] == [
            "the register stops in 1990",
            "a later account has it running to 1993",
        ]

    async def test_a_rule_that_is_not_there_is_refused(self, storage):
        timeline = await _timeline(storage)
        result, _ = await tools.end_recurrence(
            timeline.id,
            storage,
            recurrence_id="nope",
            ends_at=_dt(1990),
            because="the register stops",
            judge=ARCHIVIST,
        )
        assert result["ended"] is False
        assert "no recurrence" in result["refused"]

    async def test_each_call_writes_its_own_journal_row(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        await tools.end_recurrence(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            ends_at=_dt(1990),
            because="the register stops in 1990",
            judge=ARCHIVIST,
        )
        rows = await storage.query_decisions(kinds=[DecisionKind.RECURRENCE_BOUND])
        assert len(rows) == 1


class TestRecurrenceExceptions:
    async def test_a_cancellation_is_stored_against_the_rule(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        result, _ = await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="cancelled",
            source_id="doc-2",
            because="the register says it was called off",
            judge=ARCHIVIST,
        )
        assert result["recorded"] is True

        stored = await storage.get_timeline(timeline.id)
        exception = stored.recurrences[0].exceptions[0]
        assert exception.kind == "cancelled"
        assert exception.source_id == "doc-2"
        assert exception.judged_by.agent_id == "archivist"

        rows = await storage.query_decisions(kinds=[DecisionKind.RECURRENCE_EXCEPTION])
        assert len(rows) == 1

    async def test_an_occurrence_the_rule_does_not_produce_is_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        result, _ = await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 9),
            kind="cancelled",
            judge=ARCHIVIST,
        )
        assert result["recorded"] is False
        assert "does not produce" in result["refused"]

    async def test_a_move_with_nowhere_to_go_is_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        result, _ = await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="moved",
            judge=ARCHIVIST,
        )
        assert result["recorded"] is False
        assert "moved_to" in result["refused"]

    async def test_a_kind_that_is_neither_is_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        result, _ = await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="postponed",
            judge=ARCHIVIST,
        )
        assert result["recorded"] is False
        assert "cancelled" in result["refused"]


class TestMaterialisingThroughAddTimepoint:
    async def test_the_same_occurrence_twice_gives_one_point(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)

        first, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        second, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        assert first["materialised"] is True
        assert second["materialised"] is False
        assert first["timepoint_id"] == second["timepoint_id"]

        stored = await storage.get_timeline(timeline.id)
        assert len(stored.timepoints) == 1
        assert stored.timepoints[0].recurrence_id == recurrence_id
        assert stored.timepoints[0].occurrence_start == _dt(1890, 1, 8)
        assert stored.timepoints[0].kind == "interval"

    async def test_a_start_the_rule_does_not_produce_is_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        with pytest.raises(ValueError, match="does not produce"):
            await tools.add_timeline_timepoint(
                timeline.id,
                storage,
                recurrence_id=recurrence_id,
                occurrence_start=_dt(1890, 1, 9),
            )
        stored = await storage.get_timeline(timeline.id)
        assert stored.timepoints == []

    async def test_a_cancelled_occurrence_is_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="cancelled",
            judge=ARCHIVIST,
        )
        with pytest.raises(ValueError, match="cancelled"):
            await tools.add_timeline_timepoint(
                timeline.id,
                storage,
                recurrence_id=recurrence_id,
                occurrence_start=_dt(1890, 1, 8),
            )

    async def test_a_moved_occurrence_lands_on_its_new_date_under_its_old_identity(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="moved",
            moved_to=_dt(1890, 1, 10),
            judge=ARCHIVIST,
        )
        first, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        assert first["start"] == _dt(1890, 1, 10).isoformat()

        again, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        assert again["materialised"] is False
        assert again["timepoint_id"] == first["timepoint_id"]

        stored = await storage.get_timeline(timeline.id)
        assert len(stored.timepoints) == 1
        assert stored.timepoints[0].start == _dt(1890, 1, 10)

    async def test_dates_alongside_an_occurrence_are_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        with pytest.raises(ValueError, match="takes its dates"):
            await tools.add_timeline_timepoint(
                timeline.id,
                storage,
                start=_dt(1890, 1, 8),
                recurrence_id=recurrence_id,
                occurrence_start=_dt(1890, 1, 8),
            )

    async def test_half_an_occurrence_is_refused(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        with pytest.raises(ValueError, match="both"):
            await tools.add_timeline_timepoint(
                timeline.id, storage, occurrence_start=_dt(1890, 1, 8)
            )

    async def test_a_materialised_occurrence_takes_part_in_the_ordering_checks(self, storage):
        """A dated point can close a loop, and an occurrence is a dated point."""
        timeline = Timeline(
            name="the parish",
            timepoints=[Timepoint(id="fire", label="the fire", start=_dt(1890, 1, 15))],
        )
        await storage.store_timeline(timeline)
        recurrence_id = await _weekly(storage, timeline.id)
        materialised, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        # The dates already put the occurrence before the fire, so a source
        # asserting the opposite cannot hold.
        result, _ = await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "fire", "later_id": materialised["timepoint_id"]}],
            source_id="doc-2",
            basis="stated",
            judge=ARCHIVIST,
        )
        assert result["temporal_contradictions"]
        assert result["temporal_contradictions"][0]["kind"] == "cycle"


class TestQueryTimeline:
    async def test_a_range_query_computes_the_occurrences_in_the_window(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(
            timeline.id,
            storage,
            range_start=_dt(1890),
            range_end=_dt(1890, 1, 20),
        )
        occurrences = [point for point in result["timepoints"] if point.get("recurrence_id")]
        assert [point["occurrence_start"] for point in occurrences] == [
            _dt(1890, 1, 1).isoformat(),
            _dt(1890, 1, 8).isoformat(),
            _dt(1890, 1, 15).isoformat(),
        ]
        assert result["recurrences"][0]["recurrence_id"] == recurrence_id
        assert result["recurrences"][0]["truncated"] is False

    async def test_the_cap_fires_per_rule_and_the_response_says_so(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(
            timeline.id,
            storage,
            range_start=_dt(1890),
            range_end=_dt(1990),
            occurrence_cap=5,
        )
        assert result["recurrences"][0]["truncated"] is True
        assert result["recurrences"][0]["occurrences_returned"] == 5
        assert result["recurrences"][0]["covered_end"] == _dt(1890, 1, 29).isoformat()

    async def test_a_cap_above_the_ceiling_gets_the_ceiling(self, storage):
        from epimemer.pipelines.timeline.recurrence import OCCURRENCE_CAP

        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(
            timeline.id,
            storage,
            range_start=_dt(1890),
            range_end=_dt(1990),
            occurrence_cap=10_000,
        )
        assert result["occurrence_cap"] == OCCURRENCE_CAP
        assert result["recurrences"][0]["occurrences_returned"] == OCCURRENCE_CAP

    async def test_an_occurrence_names_the_point_that_materialised_it(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        made, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        result, _ = await tools.query_timeline(
            timeline.id,
            storage,
            range_start=_dt(1890, 1, 8),
            range_end=_dt(1890, 1, 8),
        )
        occurrences = [
            point
            for point in result["timepoints"]
            if point.get("occurrence_start") == _dt(1890, 1, 8).isoformat()
            and point.get("materialised_id") is not None
        ]
        assert occurrences[0]["materialised_id"] == made["timepoint_id"]

    async def test_next_is_answered_in_a_fictional_timelines_era(self, storage):
        timeline = await _timeline(storage, reference_time=_dt(1897, 5, 1))
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(timeline.id, storage)
        occurrences = [point for point in result["timepoints"] if point.get("recurrence_id")]
        assert result["occurrences_after"] == _dt(1897, 5, 1).isoformat()
        assert occurrences[0]["occurrence_start"] == _dt(1897, 5, 5).isoformat()

    async def test_next_after_overrides_the_timelines_own_clock(self, storage):
        timeline = await _timeline(storage, reference_time=_dt(1897, 5, 1))
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(timeline.id, storage, next_after=_dt(1890, 1, 8))
        occurrences = [point for point in result["timepoints"] if point.get("recurrence_id")]
        assert result["occurrences_after"] == _dt(1890, 1, 8).isoformat()
        assert occurrences[0]["occurrence_start"] == _dt(1890, 1, 15).isoformat()

    async def test_an_unset_reference_time_measures_next_from_the_clock(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(timeline.id, storage, now=_dt(2026, 9, 17))
        assert result["occurrences_after"] == _dt(2026, 9, 17).isoformat()

    async def test_recurrences_can_be_left_out(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(
            timeline.id,
            storage,
            range_start=_dt(1890),
            range_end=_dt(1890, 1, 20),
            include_recurrences=False,
        )
        assert result["timepoints"] == []
        assert result["recurrences"] == []

    async def test_a_nearest_query_gives_the_nearest_occurrence_of_each_rule(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        result, _ = await tools.query_timeline(timeline.id, storage, target=_dt(1890, 1, 9))
        occurrences = [point for point in result["timepoints"] if point.get("recurrence_id")]
        assert [point["occurrence_start"] for point in occurrences] == [_dt(1890, 1, 8).isoformat()]

    async def test_an_ordering_query_leaves_occurrences_out(self, storage):
        """An occurrence takes part in the order only once it is materialised."""
        timeline = Timeline(
            name="the parish",
            timepoints=[
                Timepoint(id="fire", label="the fire"),
                Timepoint(id="flood", label="the flood"),
            ],
        )
        await storage.store_timeline(timeline)
        await _weekly(storage, timeline.id)
        await tools.order_timepoints(
            timeline.id,
            storage,
            pairs=[{"earlier_id": "fire", "later_id": "flood"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        result, _ = await tools.query_timeline(timeline.id, storage, after="fire")
        assert [point["id"] for point in result["timepoints"]] == ["flood"]
        assert result["recurrences"] == []


async def _linked_to_the_rule(storage, timeline_id: str, recurrence_id: str) -> tuple[Fact, dict]:
    """A fact attached to the rule itself, with the response that attached it."""
    node = Fact(content="the service is at the parish church", source_id="doc-1")
    await storage.store_node(node)
    result, _ = await tools.create_timelink(
        node.id, timeline_id, storage, recurrence_id=recurrence_id
    )
    return node, result


async def _timelinks(storage, node_id: str) -> list:
    return [
        edge for edge in await storage.get_edges_from(node_id) if edge.type == EdgeType.TIMELINK
    ]


class TestLinkingAFactToTheRule:
    """The level a fact holds at every occurrence, rather than at one of them."""

    async def test_the_edge_names_the_rule_and_no_point(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)

        node, result = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        assert result["level"] == "recurrence"
        assert result["recurrence_id"] == recurrence_id
        assert result["label"] == "the service"
        assert result["bounds"] == {"start": None, "end": None}

        edges = await _timelinks(storage, node.id)
        assert [edge.dst_id for edge in edges] == [timeline.id]
        assert edges[0].metadata == {"recurrence_id": recurrence_id}
        assert edge_is_live(edges[0])

    async def test_the_response_reports_the_end_a_correction_moved(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        await tools.end_recurrence(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            ends_at=_dt(1893),
            because="the parish register stops",
            judge=ARCHIVIST,
        )

        _, result = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        assert result["bounds"]["end"] == _dt(1893).isoformat()

    async def test_naming_neither_level_is_refused(self, storage):
        timeline = await _timeline(storage)
        await _weekly(storage, timeline.id)
        node = Fact(content="a claim", source_id="doc-1")
        await storage.store_node(node)

        with pytest.raises(ValueError, match="Name either"):
            await tools.create_timelink(node.id, timeline.id, storage)

    async def test_naming_both_levels_is_refused(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        point, _ = await tools.add_timeline_timepoint(
            timeline.id, storage, start=_dt(1890, 6), label="the fete"
        )
        node = Fact(content="a claim", source_id="doc-1")
        await storage.store_node(node)

        with pytest.raises(ValueError, match="not both"):
            await tools.create_timelink(
                node.id,
                timeline.id,
                storage,
                timepoint_id=point["timepoint_id"],
                recurrence_id=recurrence_id,
            )
        assert await _timelinks(storage, node.id) == []

    async def test_a_rule_that_is_not_there_is_refused_naming_the_ones_that_are(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node = Fact(content="a claim", source_id="doc-1")
        await storage.store_node(node)

        with pytest.raises(ValueError, match=f"{recurrence_id} \\(the service\\)"):
            await tools.create_timelink(
                node.id, timeline.id, storage, recurrence_id="not-a-recurrence"
            )
        assert await _timelinks(storage, node.id) == []

    async def test_a_timeline_with_no_rules_says_so(self, storage):
        timeline = await _timeline(storage)
        node = Fact(content="a claim", source_id="doc-1")
        await storage.store_node(node)

        with pytest.raises(ValueError, match="no recurrence rules"):
            await tools.create_timelink(
                node.id, timeline.id, storage, recurrence_id="not-a-recurrence"
            )


class TestWhatReadsARuleLevelLink:
    """Every reader of a `TIMELINK` meets one, and none of them takes it for a point."""

    async def test_a_merge_of_two_points_leaves_it_alone(self, storage):
        timeline = Timeline(
            name="the parish",
            timepoints=[
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="go-live", label="the go-live"),
            ],
        )
        await storage.store_timeline(timeline)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        result, _ = await tools.merge_timepoints(
            timeline.id,
            storage,
            survivor_id="launch",
            merged_id="go-live",
            because="two documents, one event",
            judge=ARCHIVIST,
        )

        assert result["merged"] is True
        assert result["links_retired"] == []
        edges = await _timelinks(storage, node.id)
        assert [edge.metadata for edge in edges] == [{"recurrence_id": recurrence_id}]
        assert edge_is_live(edges[0])

    async def test_a_split_moving_links_leaves_it_alone(self, storage):
        """The helper both timepoint verdicts plan their link moves with."""
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        retired, written = await tools._plan_timelink_moves(
            storage,
            timeline_id=timeline.id,
            moves=[LinkMove(node_id=node.id, from_point_id="p1", to_point_id="p2")],
            judge=ARCHIVIST,
            at=_dt(1890),
        )

        assert (retired, written) == ([], [])
        edges = await _timelinks(storage, node.id)
        assert [edge.metadata for edge in edges] == [{"recurrence_id": recurrence_id}]

    async def test_it_is_not_counted_among_the_nodes_linked_to_a_point(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        point, _ = await tools.add_timeline_timepoint(
            timeline.id, storage, start=_dt(1890, 6), label="the fete"
        )

        found = await tools._nodes_linked_to_point(
            storage, timeline_id=timeline.id, timepoint_id=point["timepoint_id"]
        )

        assert found == []
        assert node.id not in found

    async def test_search_never_calls_it_date_contested(self, storage):
        """A rule holds no place in the order, so nothing can dispute it."""
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        assert await tools._date_contested_for([node.id], storage) == {}

    async def test_the_dashboard_snapshot_carries_it_without_a_point(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        snapshot = await assemble_snapshot(storage, storage.current_database)

        timelinks = [
            edge for edge in snapshot["edges"] if edge["edge_type"] == EdgeType.TIMELINK.value
        ]
        assert [edge["src_id"] for edge in timelinks] == [node.id]
        assert timelinks[0]["metadata"] == {"recurrence_id": recurrence_id}


def _computed(result: dict) -> list[dict]:
    """The computed occurrences in a `query_timeline` answer.

    A computed occurrence carries `materialised_id` and a stored point never
    does, so that key is what tells the two apart, including for the stored
    point that materialises an occurrence and so names a rule of its own.
    """
    return [point for point in result["timepoints"] if "materialised_id" in point]


def _stored(result: dict) -> list[dict]:
    return [point for point in result["timepoints"] if "materialised_id" not in point]


def _summary(node) -> dict:
    return {"id": node.id, "content": node.content, "node_type": "fact"}


async def _fact(storage, content: str) -> Fact:
    node = Fact(content=content, source_id="doc-1")
    await storage.store_node(node)
    return node


async def _linked_to_the_point(storage, timeline_id: str, timepoint_id: str, content: str) -> Fact:
    node = await _fact(storage, content)
    await tools.create_timelink(node.id, timeline_id, storage, timepoint_id=timepoint_id)
    return node


class TestFactsOnOccurrences:
    """What a rule-level fact looks like on the occurrences it holds at.

    A fact linked to the rule holds at every occurrence, so `query_timeline`
    lists it on each one under `linked_via_rule`, beside the `linked` list of
    facts attached to that occurrence alone.
    """

    async def test_a_rule_level_fact_is_listed_on_every_occurrence(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1890, 1, 20)
        )

        occurrences = _computed(result)
        assert [point["occurrence_start"] for point in occurrences] == [
            _dt(1890, 1, 1).isoformat(),
            _dt(1890, 1, 8).isoformat(),
            _dt(1890, 1, 15).isoformat(),
        ]
        assert [point["linked_via_rule"] for point in occurrences] == [[_summary(node)]] * 3
        assert [point["linked"] for point in occurrences] == [[], [], []]

    async def test_a_materialised_occurrence_keeps_its_own_facts_apart(self, storage):
        """Two levels, two lists: what holds at every occurrence and what holds at this one."""
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        rule_fact, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        made, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        own = await _linked_to_the_point(
            storage, timeline.id, made["timepoint_id"], "the service of 8 January was sung"
        )

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1890, 1, 20)
        )

        occurrence = next(
            point
            for point in _computed(result)
            if point["occurrence_start"] == _dt(1890, 1, 8).isoformat()
        )
        assert occurrence["materialised_id"] == made["timepoint_id"]
        assert occurrence["linked"] == [_summary(own)]
        assert occurrence["linked_via_rule"] == [_summary(rule_fact)]

        others = [
            point
            for point in _computed(result)
            if point["occurrence_start"] != _dt(1890, 1, 8).isoformat()
        ]
        assert [point["linked"] for point in others] == [[], []]

    async def test_a_cancelled_occurrence_is_absent_so_nothing_is_shown_for_it(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="cancelled",
            because="the church was flooded",
            judge=ARCHIVIST,
        )

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1890, 1, 20)
        )

        assert [point["occurrence_start"] for point in _computed(result)] == [
            _dt(1890, 1, 1).isoformat(),
            _dt(1890, 1, 15).isoformat(),
        ]

    async def test_a_moved_occurrence_is_returned_at_its_new_date_with_the_rules_facts(
        self, storage
    ):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        await tools.record_recurrence_exception(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="moved",
            moved_to=_dt(1890, 1, 9),
            because="the vicar was away",
            judge=ARCHIVIST,
        )

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1890, 1, 20)
        )

        moved = next(point for point in _computed(result) if point["moved"])
        assert moved["occurrence_start"] == _dt(1890, 1, 8).isoformat()
        assert moved["start"] == _dt(1890, 1, 9).isoformat()
        assert moved["linked_via_rule"] == [_summary(node)]

    async def test_a_narrower_validity_window_does_not_filter_the_fact_out(self, storage):
        """A timeline query is not a validity query: `search(valid_as_of=...)` is.

        The fact holds at every occurrence because a judge linked it to the
        rule, and the validity its source gave it is a separate claim about when
        it was true. Filtering here would quietly answer the other question.
        """
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        await storage.store_edge(
            NodeEdge(
                src_id=node.id,
                dst_id="doc-1",
                type=EdgeType.SOURCED_FROM,
                validity=[
                    ValidityInterval(
                        start=PreciseInstant(at=_dt(1890, 1, 8)),
                        end=PreciseInstant(at=_dt(1890, 1, 9)),
                        basis=IntervalBasis.STATED,
                    )
                ],
                judged_by=ARCHIVIST,
            )
        )

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1890, 1, 20)
        )

        assert [point["linked_via_rule"] for point in _computed(result)] == [[_summary(node)]] * 3

    async def test_a_retired_fact_and_a_retired_link_are_both_skipped(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)

        retired_node, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        await storage.store_node(retired_node.model_copy(update={"status": NodeStatus.HISTORICAL}))

        live_node = await _fact(storage, "the service is sung in Latin")
        await tools.create_timelink(live_node.id, timeline.id, storage, recurrence_id=recurrence_id)
        detached = await _fact(storage, "the service was held in the school")
        await tools.create_timelink(detached.id, timeline.id, storage, recurrence_id=recurrence_id)
        for edge in await _timelinks(storage, detached.id):
            await storage.store_edge(
                edge.model_copy(update={"retired_at": _dt(1891), "retired_by": ARCHIVIST})
            )

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1890, 1, 10)
        )

        assert [point["linked_via_rule"] for point in _computed(result)] == [
            [_summary(live_node)],
            [_summary(live_node)],
        ]

    async def test_a_stored_point_with_no_rule_carries_linked_too(self, storage):
        """One field for the facts attached here, whatever kind of point it is."""
        timeline = await _timeline(storage)
        point, _ = await tools.add_timeline_timepoint(
            timeline.id, storage, start=_dt(1890, 6), label="the fete"
        )
        node = await _linked_to_the_point(
            storage, timeline.id, point["timepoint_id"], "the fete was rained off"
        )

        result, _ = await tools.query_timeline(
            timeline.id, storage, range_start=_dt(1890), range_end=_dt(1891)
        )

        stored = _stored(result)
        assert [entry["id"] for entry in stored] == [point["timepoint_id"]]
        assert stored[0]["linked"] == [_summary(node)]

    async def test_nearest_mode_carries_the_same_lists(self, storage):
        timeline = await _timeline(storage)
        recurrence_id = await _weekly(storage, timeline.id)
        rule_fact, _ = await _linked_to_the_rule(storage, timeline.id, recurrence_id)
        made, _ = await tools.add_timeline_timepoint(
            timeline.id,
            storage,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
        )
        own = await _linked_to_the_point(
            storage, timeline.id, made["timepoint_id"], "the service of 8 January was sung"
        )

        result, _ = await tools.query_timeline(timeline.id, storage, target=_dt(1890, 1, 9))

        occurrence = _computed(result)[0]
        assert occurrence["occurrence_start"] == _dt(1890, 1, 8).isoformat()
        assert occurrence["linked"] == [_summary(own)]
        assert occurrence["linked_via_rule"] == [_summary(rule_fact)]
