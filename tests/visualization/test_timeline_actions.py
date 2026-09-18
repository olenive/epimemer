"""Timeline decisions reach the live log, carrying the kind they were journalled as.

Six decisions used to pass the log in silence (EVENT_LOG.md §9): a timeline
write lands through `store_timeline`, which replaces the record whole, and the
`TIMELINK` retirement a split or a merge performs lands through `store_edge`,
and neither was a transaction boundary. A viewer watching the dashboard saw the
graph change shape with nothing saying who did it or why.

The verb set stays closed (§11). A timeline act carries its `DecisionKind` as
its verb, so the live log and the journal keep speaking one vocabulary, which is
the whole reason the verb set is small. Nothing was added to `ActionVerb`.

Both backends, through the `storage` fixture: the acts are emitted at the sixth
`_tx` boundary, so each backend's implementation of it is under test here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from epimemer.core.types import (
    DecisionKind,
    EdgeType,
    Fact,
    JudgeRef,
    NodeEdge,
    Timeline,
    Timepoint,
)
from epimemer.mcp import tools
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import ActionVerb, GraphActionRecorded
from epimemer.visualization.instrumented_storage import instrument_storage

ARCHIVIST = JudgeRef(agent_id="archivist", digest="d1")

# The six decisions a timeline tool records, and nothing else. Named here so a
# seventh kind reaching the log has to be added deliberately.
TIMELINE_KINDS = frozenset(
    {
        DecisionKind.TEMPORAL_ORDER,
        DecisionKind.TEMPORAL_VERDICT,
        DecisionKind.TIMEPOINT_MERGE,
        DecisionKind.RECURRENCE,
        DecisionKind.RECURRENCE_BOUND,
        DecisionKind.RECURRENCE_EXCEPTION,
    }
)


def _dt(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


@pytest.fixture
def bus():
    return create_event_bus()


@pytest.fixture
def acts(bus) -> list[GraphActionRecorded]:
    recorded: list[GraphActionRecorded] = []
    bus.subscribe(GraphActionRecorded, handler=lambda e: recorded.append(e))
    return recorded


@pytest.fixture
def watched(storage, bus):
    """The `storage` fixture's backend, instrumented. Runs on both backends."""
    return instrument_storage(storage, bus)


async def _timeline(watched, points=()) -> Timeline:
    timeline = Timeline(name="the parish", timepoints=list(points))
    await watched.store_timeline(timeline)
    return timeline


async def _disputed(watched) -> tuple[str, str]:
    """A timeline whose two sources disagree about the order of two points."""
    timeline = await _timeline(
        watched,
        [Timepoint(id="fire", label="the fire"), Timepoint(id="flood", label="the flood")],
    )
    await tools.order_timepoints(
        timeline.id,
        watched,
        pairs=[{"earlier_id": "fire", "later_id": "flood"}],
        source_id="doc-1",
        basis="stated",
        judge=ARCHIVIST,
    )
    result, _ = await tools.order_timepoints(
        timeline.id,
        watched,
        pairs=[{"earlier_id": "flood", "later_id": "fire"}],
        source_id="doc-2",
        basis="stated",
        judge=ARCHIVIST,
    )
    return timeline.id, result["temporal_contradictions"][0]["temporal_contradiction_id"]


async def _linked_fact(watched, timeline_id: str, timepoint_id: str, content: str) -> Fact:
    node = Fact(content=content, source_id="seg-1")
    await watched.store_node(node)
    await watched.store_edge(
        NodeEdge(
            src_id=node.id,
            dst_id=timeline_id,
            type=EdgeType.TIMELINK,
            metadata={"timepoint_id": timepoint_id},
        )
    )
    return node


async def _weekly(watched, timeline_id: str) -> str:
    result, _ = await tools.add_recurrence(
        timeline_id,
        watched,
        label="the service",
        duration=timedelta(hours=1),
        anchor=_dt(1890),
        period=timedelta(days=7),
        source_id="doc-1",
        judge=ARCHIVIST,
    )
    return result["recurrence_id"]


def _only(acts: list[GraphActionRecorded]) -> GraphActionRecorded:
    assert len(acts) == 1, [act.summary for act in acts]
    return acts[0]


class TestTheSixDecisionsReachTheLog:
    """One act per decision, verb the kind the journal recorded, subject the
    timeline, and the judge the tool named."""

    async def test_ordering_timepoints_is_one_act(self, watched, acts):
        timeline = await _timeline(
            watched,
            [
                Timepoint(id="p1", label="p1"),
                Timepoint(id="p2", label="p2"),
                Timepoint(id="p3", label="p3"),
            ],
        )
        acts.clear()

        await tools.order_timepoints(
            timeline.id,
            watched,
            pairs=[
                {"earlier_id": "p1", "later_id": "p2"},
                {"earlier_id": "p2", "later_id": "p3"},
            ],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )

        act = _only(acts)
        assert act.verb is DecisionKind.TEMPORAL_ORDER
        assert act.subjects == [timeline.id]
        assert act.counts["constraints"] == 2
        assert act.judged_by == "archivist"

    async def test_a_refused_ordering_emits_nothing(self, watched, acts):
        """Nothing was written, so there is no act. A log line for a refusal
        would say the graph changed when it did not."""
        timeline = await _timeline(watched, [Timepoint(id="fire", label="the fire")])
        acts.clear()

        await tools.order_timepoints(
            timeline.id,
            watched,
            pairs=[],
            source_id="doc-1",
            basis="world knowledge",
            judge=ARCHIVIST,
        )

        assert acts == []

    async def test_answering_a_contradiction_is_one_act(self, watched, acts):
        timeline_id, contradiction_id = await _disputed(watched)
        stored = await watched.get_timeline(timeline_id)
        doomed = stored.temporal_contradictions[0].constraint_ids[0]
        acts.clear()

        await tools.resolve_temporal_contradiction(
            timeline_id,
            watched,
            contradiction_id=contradiction_id,
            verdict="retire_constraint",
            constraint_id=doomed,
            because="the second source narrates rather than orders",
            judge=ARCHIVIST,
        )

        act = _only(acts)
        assert act.verb is DecisionKind.TEMPORAL_VERDICT
        assert act.subjects == [timeline_id]
        assert act.judged_by == "archivist"

    async def test_a_split_is_one_act_covering_every_link_it_moved(self, watched, acts):
        """The visible case §9 named: a split moves a fact from one date to
        another, retiring its link and writing a new one. That is one decision,
        so it is one line, not one per edge."""
        timeline_id, contradiction_id = await _disputed(watched)
        moving = await _linked_fact(watched, timeline_id, "fire", "The fire took the roof")
        await _linked_fact(watched, timeline_id, "fire", "The fire was seen from the hill")
        stored = await watched.get_timeline(timeline_id)
        acts.clear()

        result, _ = await tools.resolve_temporal_contradiction(
            timeline_id,
            watched,
            contradiction_id=contradiction_id,
            verdict="not_the_same_event",
            point_id="fire",
            constraints_to_move=[stored.temporal_contradictions[0].constraint_ids[0]],
            nodes_to_move=[moving.id],
            because="two fires, and each source saw a different one",
            judge=ARCHIVIST,
        )

        assert len(result["links_retired"]) == 1
        assert len(result["links_written"]) == 1
        act = _only(acts)
        assert act.verb is DecisionKind.TEMPORAL_VERDICT
        assert act.counts["retired_links"] == 1
        assert act.counts["new_links"] == 1
        assert act.counts["points"] == 1

    async def test_merging_timepoints_is_one_act_covering_every_link(self, watched, acts):
        timeline = await _timeline(
            watched,
            [
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="golive", label="the go-live"),
            ],
        )
        for n in range(2):
            await _linked_fact(watched, timeline.id, "golive", f"note {n}")
        acts.clear()

        await tools.merge_timepoints(
            timeline.id,
            watched,
            survivor_id="launch",
            merged_id="golive",
            because="two documents, one event",
            judge=ARCHIVIST,
        )

        act = _only(acts)
        assert act.verb is DecisionKind.TIMEPOINT_MERGE
        assert act.subjects == [timeline.id]
        assert act.counts["retired_links"] == 2
        assert act.counts["new_links"] == 2
        assert act.judged_by == "archivist"

    async def test_a_refused_merge_emits_nothing(self, watched, acts):
        timeline = await _timeline(
            watched,
            [
                Timepoint(id="launch", label="the launch"),
                Timepoint(id="golive", label="the go-live"),
            ],
        )
        await tools.order_timepoints(
            timeline.id,
            watched,
            pairs=[{"earlier_id": "launch", "later_id": "golive"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )
        acts.clear()

        result, _ = await tools.merge_timepoints(
            timeline.id,
            watched,
            survivor_id="launch",
            merged_id="golive",
            because="one event",
            judge=ARCHIVIST,
        )

        assert result["merged"] is False
        assert acts == []

    async def test_adding_a_recurrence_is_one_act(self, watched, acts):
        timeline = await _timeline(watched)
        acts.clear()

        await _weekly(watched, timeline.id)

        act = _only(acts)
        assert act.verb is DecisionKind.RECURRENCE
        assert act.subjects == [timeline.id]
        assert act.judged_by == "archivist"

    async def test_ending_a_recurrence_is_one_act(self, watched, acts):
        timeline = await _timeline(watched)
        recurrence_id = await _weekly(watched, timeline.id)
        acts.clear()

        await tools.end_recurrence(
            timeline.id,
            watched,
            recurrence_id=recurrence_id,
            ends_at=_dt(1893),
            because="the chapel closed",
            judge=ARCHIVIST,
        )

        act = _only(acts)
        assert act.verb is DecisionKind.RECURRENCE_BOUND
        assert act.subjects == [timeline.id]
        assert act.counts["bound_changes"] == 1

    async def test_recording_an_exception_is_one_act(self, watched, acts):
        timeline = await _timeline(watched)
        recurrence_id = await _weekly(watched, timeline.id)
        acts.clear()

        await tools.record_recurrence_exception(
            timeline.id,
            watched,
            recurrence_id=recurrence_id,
            occurrence_start=_dt(1890, 1, 8),
            kind="cancelled",
            because="the snow",
            judge=ARCHIVIST,
        )

        act = _only(acts)
        assert act.verb is DecisionKind.RECURRENCE_EXCEPTION
        assert act.subjects == [timeline.id]
        assert act.counts["exceptions"] == 1


class TestPlacingAMarkIsNotADecision:
    """The four tools that place a mark name no judge and emit no act, exactly
    as before. Creating a timeline, setting its present, putting a point on it
    and linking a fact to one are records of where something sits, not
    judgments about it, and a log line for each would bury the six that are."""

    async def test_create_timeline_emits_no_act(self, watched, acts):
        await tools.create_timeline("the parish", watched)
        assert acts == []

    async def test_set_reference_time_emits_no_act(self, watched, acts):
        timeline = await _timeline(watched)
        acts.clear()

        await tools.set_reference_time(timeline.id, watched, reference_time=_dt(1890))

        assert acts == []

    async def test_add_timepoint_emits_no_act(self, watched, acts):
        timeline = await _timeline(watched, [Timepoint(id="fire", start=_dt(1899))])
        acts.clear()

        await tools.add_timeline_timepoint(timeline.id, watched, start=_dt(1901))

        assert acts == []

    async def test_create_timelink_emits_no_act(self, watched, acts):
        timeline = await _timeline(watched, [Timepoint(id="fire", label="the fire")])
        node = Fact(content="The fire took the roof", source_id="seg-1")
        await watched.store_node(node)
        acts.clear()

        await tools.create_timelink(node.id, timeline.id, watched, timepoint_id="fire")

        assert acts == []


class TestImportIsSilent:
    """A restore replays somebody else's decisions; it makes none. It already
    reads as one `stored` act for the whole bundle, and no timeline decision
    may be re-announced from it."""

    async def test_restoring_timelines_announces_no_decision(self, watched, acts):
        timeline = Timeline(name="the parish", timepoints=[Timepoint(id="fire", start=_dt(1899))])
        acts.clear()

        await watched.write_verbatim_tx(timelines=[timeline])

        assert [act.verb for act in acts] == [ActionVerb.STORED]
        assert not any(act.verb in TIMELINE_KINDS for act in acts)


class TestTheVerbSetStaysClosed:
    """§11. The six kinds are the journal's vocabulary for these decisions, so
    the log borrows them rather than minting six more verbs beside them."""

    def test_no_timeline_verb_was_added(self):
        assert {verb.value for verb in ActionVerb} == {
            "stored",
            "corrected",
            "world_changed",
            "merged",
            "archived",
            "restored",
            "undetermined",
        }

    def test_there_is_still_no_superseded_verb(self):
        assert "superseded" not in {verb.value for verb in ActionVerb}
        assert "superseded" not in {kind.value for kind in DecisionKind}

    async def test_a_timeline_act_reads_as_a_line(self, watched, acts):
        """`summary` is pre-rendered on the emitting side, so a timeline act has
        to say something a person can read. "temporal_order 1 node" would be
        two errors: the subject is a timeline, and the verb is not English."""
        timeline = await _timeline(
            watched,
            [Timepoint(id="p1", label="p1"), Timepoint(id="p2", label="p2")],
        )
        acts.clear()

        await tools.order_timepoints(
            timeline.id,
            watched,
            pairs=[{"earlier_id": "p1", "later_id": "p2"}],
            source_id="doc-1",
            basis="stated",
            judge=ARCHIVIST,
        )

        summary = _only(acts).summary
        assert summary.startswith("ordered timepoints on ")
        assert timeline.id[:8] in summary
        assert "1 constraint)" in summary
        assert "node" not in summary
