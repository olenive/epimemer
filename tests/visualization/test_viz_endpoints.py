"""Tests for viz-only storage methods, snapshot assembly, and view conversion.

The HTTP/relay layer moved to the standalone hub (`test_hub.py`); the read-side
assembly that used to live in the embedded server's `/api/snapshot` and
`/api/graphs` handlers now lives in `visualization/snapshot.py` and is tested
here directly, against storage.
"""

from datetime import UTC, datetime, timedelta

import pytest

from epimemer.core.temporal import (
    NamedInstant,
    PreciseInstant,
    UnboundedInstant,
    UnknownInstant,
    ValidityInterval,
)
from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    IntervalBasis,
    NodeEdge,
    NodeStatus,
    PeriodicRule,
    RelationLabel,
    Timeline,
    Topic,
    ValueSignal,
)
from epimemer.pipelines.timeline.functions import add_timepoint
from epimemer.pipelines.timeline.ordering import add_constraints
from epimemer.pipelines.timeline.recurrence import (
    OCCURRENCE_CAP,
    add_recurrence,
    materialise,
    record_exception,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.storage.surrealdb_adapter import SurrealDBStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import EdgeStored, edge_to_view, node_to_view
from epimemer.visualization.instrumented_storage import instrument_storage
from epimemer.visualization.snapshot import assemble_snapshot, list_graphs_result


@pytest.fixture
def storage():
    return InMemoryStorage()


# --- viz_list_nodes / viz_list_edges ---


class TestVizStorageMethods:
    async def test_viz_list_nodes_returns_active_by_default(self, storage):
        t1 = Topic(content="Active topic", source_id="s1")
        t2 = Topic(content="Superseded topic", source_id="s1", status=NodeStatus.SUPERSEDED)
        await storage.store_node(t1)
        await storage.store_node(t2)

        nodes = await storage.viz_list_nodes("default")
        assert len(nodes) == 1
        assert nodes[0].content == "Active topic"

    async def test_viz_list_nodes_with_historical_status(self, storage):
        t1 = Topic(content="Active", source_id="s1")
        t2 = Topic(content="Superseded", source_id="s1", status=NodeStatus.SUPERSEDED)
        await storage.store_node(t1)
        await storage.store_node(t2)

        nodes = await storage.viz_list_nodes("default", historical_status=NodeStatus.SUPERSEDED)
        assert len(nodes) == 1
        assert nodes[0].content == "Superseded"

    async def test_viz_list_edges(self, storage):
        edge = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        await storage.store_edge(edge)

        edges = await storage.viz_list_edges("default")
        assert len(edges) == 1
        assert edges[0].src_id == "a"

    async def test_viz_list_nodes_cross_graph(self, storage):
        """Viz reads from a different graph without switching active."""
        t1 = Topic(content="Default topic", source_id="s1")
        await storage.store_node(t1)

        await storage.switch_database("other")
        t2 = Topic(content="Other topic", source_id="s1")
        await storage.store_node(t2)
        await storage.switch_database("default")

        # Read from "other" without switching
        nodes = await storage.viz_list_nodes("other")
        assert len(nodes) == 1
        assert nodes[0].content == "Other topic"

        # Active database unchanged
        assert storage.current_database == "default"

    async def test_viz_list_nodes_nonexistent_graph(self, storage):
        nodes = await storage.viz_list_nodes("no-such-graph")
        assert nodes == []

    async def test_viz_list_edges_nonexistent_graph(self, storage):
        edges = await storage.viz_list_edges("no-such-graph")
        assert edges == []


# --- Snapshot / graph-list assembly (was the embedded server's HTTP handlers) ---


class TestSnapshotAssembly:
    async def test_list_graphs_result_includes_backend(self, storage):
        result = await list_graphs_result(storage)
        assert result["graphs"] == ["default"]
        assert result["active_graph"] == "default"
        assert result["backend"] == "memory"

    async def test_list_graphs_result_seeds_reflection_pressure(self, storage):
        """A browser connecting mid-session needs the current numbers, not just
        the events that happen after it arrives."""
        await storage.bump_reflect_counter()
        await storage.bump_reflect_counter()

        result = await list_graphs_result(storage, default_reflect_threshold=10)

        assert result["reflect"] == {
            "count": 2,
            "threshold": 10,
            "suggested": False,
        }

    async def test_list_graphs_result_reports_a_due_reflect(self, storage):
        await storage.bump_reflect_counter()

        result = await list_graphs_result(storage, default_reflect_threshold=1)

        assert result["reflect"]["suggested"] is True

    async def test_list_graphs_result_honours_a_threshold_override(self, storage):
        await storage.set_reflect_threshold_override(3)

        result = await list_graphs_result(storage, default_reflect_threshold=10)

        assert result["reflect"]["threshold"] == 3

    async def test_assemble_snapshot_returns_node_and_edge_views(self, storage):
        t = Topic(content="Test topic", source_id="s1")
        await storage.store_node(t)
        e = NodeEdge(src_id=t.id, dst_id=t.id, type=EdgeType.SUPPORTS)
        await storage.store_edge(e)

        data = await assemble_snapshot(storage, "default")
        assert data["graph"] == "default"
        assert len(data["nodes"]) == 1
        assert len(data["edges"]) == 1
        node = data["nodes"][0]
        assert "node_id" in node and "node_type" in node and "confidence" in node
        edge = data["edges"][0]
        assert "edge_id" in edge and "edge_type" in edge

    async def test_assemble_snapshot_includes_timelines_with_timepoints(self, storage):
        timeline, _ = add_timepoint(
            Timeline(name="History"),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            label="the beginning",
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [view] = data["timelines"]
        assert view["name"] == "History"
        assert view["timepoints"][0]["label"] == "the beginning"
        # Serialized for the wire, so datetimes must already be strings.
        assert isinstance(view["timepoints"][0]["start"], str)

    async def test_assemble_snapshot_keeps_vague_timepoints_undated(self, storage):
        timeline, _ = add_timepoint(Timeline(name="History"), label="long ago")
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        assert data["timelines"][0]["timepoints"][0]["start"] is None

    async def test_assemble_snapshot_includes_relation_labels(self, storage):
        """An edge carries its label as a bare string, so the vocabulary's
        descriptions live nowhere a viewer can reach from the edge alone.
        They ride along for the reason metacontexts do."""
        await storage.store_relation_label(
            RelationLabel(
                name="advised",
                kind="relationship",
                description="Retained counsel, not employment.",
            )
        )

        data = await assemble_snapshot(storage, "default")

        [view] = data["relation_labels"]
        assert view["name"] == "advised"
        assert view["kind"] == "relationship"
        assert view["description"] == "Retained counsel, not employment."
        assert view["graph"] == "default"

    async def test_assemble_snapshot_empty_graph(self, storage):
        data = await assemble_snapshot(storage, "default")
        assert data["nodes"] == []
        assert data["edges"] == []
        assert data["timelines"] == []
        assert data["relation_labels"] == []

    async def test_assemble_snapshot_does_not_switch_active_graph(self, storage):
        await storage.switch_database("other")
        await storage.switch_database("default")
        assert storage.current_database == "default"

        await assemble_snapshot(storage, "other")
        assert storage.current_database == "default"


# --- Order, dispute and recurrence in the snapshot ---


def _dated(timeline: Timeline, start: datetime, label: str) -> tuple[Timeline, str]:
    timeline, point = add_timepoint(timeline, start=start, label=label)
    return timeline, point.id


class TestSnapshotOrderAndRecurrence:
    """What the panel needs in order to draw bounds, disputes and occurrences.

    All of it is a read-time answer computed from the timeline record, through
    the same pure functions `query_timeline` calls, so the dashboard and the
    tool can never give two answers about one point.
    """

    async def test_a_partly_dated_point_carries_its_derived_bounds(self, storage):
        timeline = Timeline(name="History")
        timeline, before_id = _dated(timeline, datetime(1890, 1, 1, tzinfo=UTC), "the fire")
        timeline, after_id = _dated(timeline, datetime(1900, 1, 1, tzinfo=UTC), "the flood")
        timeline, vague = add_timepoint(timeline, label="the quarrel")
        timeline, _ = add_constraints(
            timeline,
            [(before_id, vague.id), (vague.id, after_id)],
            source_id="s1",
            basis=IntervalBasis.STATED,
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [point] = [p for p in data["timelines"][0]["timepoints"] if p["timepoint_id"] == vague.id]
        assert point["kind"] == "vague"
        assert point["earliest"] == "1890-01-01T00:00:00Z"
        assert point["latest"] == "1900-01-01T00:00:00Z"
        assert point["contested"] is False

    async def test_a_point_nothing_constrains_has_no_bounds(self, storage):
        timeline, _ = add_timepoint(Timeline(name="History"), label="long ago")
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [point] = data["timelines"][0]["timepoints"]
        assert point["earliest"] is None
        assert point["latest"] is None

    async def test_a_dated_point_carries_its_kind_and_no_bounds(self, storage):
        timeline, _ = add_timepoint(
            Timeline(name="History"),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            end=datetime(2024, 2, 1, tzinfo=UTC),
            label="the siege",
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [point] = data["timelines"][0]["timepoints"]
        assert point["kind"] == "interval"
        assert point["earliest"] is None and point["latest"] is None

    async def test_a_contested_point_carries_the_flag_and_the_contradiction(self, storage):
        """Two sources whose orders close a loop. Both points are in dispute."""
        timeline = Timeline(name="History")
        timeline, first = add_timepoint(timeline, label="the fire")
        timeline, second = add_timepoint(timeline, label="the flood")
        timeline, _ = add_constraints(
            timeline,
            [(first.id, second.id)],
            source_id="s1",
            basis=IntervalBasis.STATED,
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        timeline, report = add_constraints(
            timeline,
            [(second.id, first.id)],
            source_id="s2",
            basis=IntervalBasis.STATED,
            at=datetime(2024, 1, 2, tzinfo=UTC),
        )
        [contradiction] = report.opened
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        points = {p["timepoint_id"]: p for p in data["timelines"][0]["timepoints"]}
        assert points[first.id]["contested"] is True
        assert points[first.id]["temporal_contradiction_id"] == contradiction.id
        assert points[second.id]["contested"] is True

    async def test_an_undisputed_point_names_no_contradiction(self, storage):
        timeline, _ = add_timepoint(Timeline(name="History"), label="long ago")
        await storage.store_timeline(timeline)

        [point] = (await assemble_snapshot(storage, "default"))["timelines"][0]["timepoints"]
        assert point["contested"] is False
        assert point["temporal_contradiction_id"] is None

    async def test_a_rules_occurrences_ride_along_with_the_timeline(self, storage):
        timeline = Timeline(name="Parish")
        timeline, _ = _dated(timeline, datetime(1897, 1, 1, tzinfo=UTC), "the first service")
        timeline, _ = _dated(timeline, datetime(1897, 1, 20, tzinfo=UTC), "the last service")
        timeline, recurrence = add_recurrence(
            timeline,
            label="the weekly service",
            rule=PeriodicRule(anchor=datetime(1897, 1, 1, tzinfo=UTC), period=timedelta(days=7)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [rule] = data["timelines"][0]["recurrences"]
        assert rule["recurrence_id"] == recurrence.id
        assert rule["label"] == "the weekly service"
        assert rule["rule_kind"] == "periodic"
        starts = [o["occurrence_start"] for o in rule["occurrences"]]
        assert "1897-01-01T00:00:00Z" in starts
        assert "1897-01-15T00:00:00Z" in starts
        assert rule["truncated"] is False

    async def test_the_occurrence_cap_is_the_one_query_timeline_uses(self, storage):
        timeline = Timeline(name="Market")
        timeline, _ = _dated(timeline, datetime(2000, 1, 1, tzinfo=UTC), "the first market")
        timeline, _ = _dated(timeline, datetime(2010, 1, 1, tzinfo=UTC), "the last market")
        timeline, _ = add_recurrence(
            timeline,
            label="market day",
            rule=PeriodicRule(anchor=datetime(2000, 1, 1, tzinfo=UTC), period=timedelta(days=1)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [rule] = data["timelines"][0]["recurrences"]
        assert len(rule["occurrences"]) == OCCURRENCE_CAP
        assert rule["truncated"] is True

    async def test_a_cancelled_occurrence_is_absent(self, storage):
        timeline = Timeline(name="Parish")
        timeline, _ = _dated(timeline, datetime(1897, 1, 1, tzinfo=UTC), "the first service")
        timeline, _ = _dated(timeline, datetime(1897, 1, 20, tzinfo=UTC), "the last service")
        timeline, recurrence = add_recurrence(
            timeline,
            label="the weekly service",
            rule=PeriodicRule(anchor=datetime(1897, 1, 1, tzinfo=UTC), period=timedelta(days=7)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        timeline, _ = record_exception(
            timeline,
            recurrence_id=recurrence.id,
            occurrence_start=datetime(1897, 1, 8, tzinfo=UTC),
            kind="cancelled",
            because="the roof fell in",
            at=datetime(2024, 1, 2, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [rule] = data["timelines"][0]["recurrences"]
        starts = [o["occurrence_start"] for o in rule["occurrences"]]
        assert "1897-01-08T00:00:00Z" not in starts
        assert "1897-01-15T00:00:00Z" in starts

    async def test_a_moved_occurrence_says_where_it_went(self, storage):
        timeline = Timeline(name="Parish")
        timeline, _ = _dated(timeline, datetime(1897, 1, 1, tzinfo=UTC), "the first service")
        timeline, _ = _dated(timeline, datetime(1897, 1, 20, tzinfo=UTC), "the last service")
        timeline, recurrence = add_recurrence(
            timeline,
            label="the weekly service",
            rule=PeriodicRule(anchor=datetime(1897, 1, 1, tzinfo=UTC), period=timedelta(days=7)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        timeline, _ = record_exception(
            timeline,
            recurrence_id=recurrence.id,
            occurrence_start=datetime(1897, 1, 8, tzinfo=UTC),
            kind="moved",
            moved_to=datetime(1897, 1, 9, tzinfo=UTC),
            at=datetime(2024, 1, 2, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [rule] = data["timelines"][0]["recurrences"]
        [moved] = [
            o for o in rule["occurrences"] if o["occurrence_start"] == "1897-01-08T00:00:00Z"
        ]
        assert moved["moved_to"] == "1897-01-09T00:00:00Z"
        assert moved["start"] == "1897-01-09T00:00:00Z"

    async def test_a_materialised_occurrence_names_its_timepoint(self, storage):
        timeline = Timeline(name="Parish")
        timeline, _ = _dated(timeline, datetime(1897, 1, 1, tzinfo=UTC), "the first service")
        timeline, _ = _dated(timeline, datetime(1897, 1, 20, tzinfo=UTC), "the last service")
        timeline, recurrence = add_recurrence(
            timeline,
            label="the weekly service",
            rule=PeriodicRule(anchor=datetime(1897, 1, 1, tzinfo=UTC), period=timedelta(days=7)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        timeline, report = materialise(
            timeline,
            recurrence_id=recurrence.id,
            occurrence_start=datetime(1897, 1, 15, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        data = await assemble_snapshot(storage, "default")

        [rule] = data["timelines"][0]["recurrences"]
        by_start = {o["occurrence_start"]: o for o in rule["occurrences"]}
        assert by_start["1897-01-15T00:00:00Z"]["materialised_id"] == report.timepoint_id
        assert by_start["1897-01-01T00:00:00Z"]["materialised_id"] is None

    async def test_a_plain_timeline_gains_only_empty_and_null_fields(self, storage):
        """Nothing the frontend already reads changes shape.

        A timeline with no order, no dispute and no rule has to serialise the
        way it did before, or every mark the panel draws today moves.
        """
        timeline, _ = add_timepoint(
            Timeline(name="History"),
            start=datetime(2024, 1, 1, tzinfo=UTC),
            label="the beginning",
        )
        await storage.store_timeline(timeline)

        [view] = (await assemble_snapshot(storage, "default"))["timelines"]

        assert view["recurrences"] == []
        [point] = view["timepoints"]
        assert point["start"] == "2024-01-01T00:00:00Z"
        assert point["label"] == "the beginning"
        assert point["kind"] == "instant"
        assert point["earliest"] is None
        assert point["latest"] is None
        assert point["contested"] is False
        assert point["temporal_contradiction_id"] is None

    async def test_a_rule_on_a_timeline_with_no_dates_enumerates_nothing(self, storage):
        """A snapshot has no query window, so it takes one from the dated points.

        With none, and no stated present either, there is nothing to centre a
        window on, and enumerating from the wall clock would put a snapshot's
        occurrences somewhere the graph never said.
        """
        timeline, _ = add_recurrence(
            Timeline(name="Parish"),
            label="the weekly service",
            rule=PeriodicRule(anchor=datetime(1897, 1, 1, tzinfo=UTC), period=timedelta(days=7)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        [rule] = (await assemble_snapshot(storage, "default"))["timelines"][0]["recurrences"]
        assert rule["occurrences"] == []

    async def test_a_stated_present_is_window_enough(self, storage):
        timeline, _ = add_recurrence(
            Timeline(name="Parish", reference_time=datetime(1897, 5, 1, tzinfo=UTC)),
            label="the weekly service",
            rule=PeriodicRule(anchor=datetime(1897, 1, 1, tzinfo=UTC), period=timedelta(days=7)),
            at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        await storage.store_timeline(timeline)

        [rule] = (await assemble_snapshot(storage, "default"))["timelines"][0]["recurrences"]
        assert len(rule["occurrences"]) > 0


# --- Conversion helper tests ---


class TestViewConversion:
    def test_node_to_view_topic(self):
        t = Topic(content="A topic", source_id="s1")
        view = node_to_view(t, "my-graph")
        assert view.node_id == t.id
        assert view.node_type == "topic"
        assert view.content == "A topic"
        assert view.status == "active"
        assert view.graph == "my-graph"
        assert view.source_id == "s1"
        assert view.extraction_method == "unspecified"
        assert view.confidence is None  # nothing has rated it

    def test_the_view_does_not_carry_novelty(self):
        """The frontend contract drops it with the field.

        `NodeView` is the single shape both the event stream and the snapshot
        endpoints hand the frontend, so a field left here would keep the tooltip
        rendering `novelty 1.00` on every node long after nothing produced it.
        """
        view = node_to_view(Topic(content="A topic", source_id="s1"), "g")

        assert "novelty" not in view.model_dump()

    def test_an_unrated_node_reaches_the_frontend_as_null(self):
        """The view relays absence rather than substituting the default.

        Reading `None` as 0.5 here would put a number on the panel's tooltip
        that no agent ever supplied — a false statement about the graph, made
        to save the frontend a `??`. The panel renders a dash instead, the same
        way it already renders "never" for an unretrieved node.
        """
        node = Topic(content="A topic", source_id="s1")
        assert node.value.confidence is None

        view = node_to_view(node, "g")

        assert view.confidence is None

    def test_a_rated_node_still_reaches_the_frontend_as_its_number(self):
        node = Topic(
            content="A topic",
            source_id="s1",
            value=ValueSignal(confidence=0.9),
        )

        assert node_to_view(node, "g").confidence == pytest.approx(0.9)

    def test_node_to_view_fact(self):
        f = Fact(content="A fact", source_id="s2")
        view = node_to_view(f, "g")
        assert view.node_type == "fact"

    def test_node_to_view_inference(self):
        i = Inference(content="An inference", source_id="s3")
        view = node_to_view(i, "g")
        assert view.node_type == "inference"

    def test_node_to_view_superseded(self):
        t = Topic(content="Old", source_id="s1", status=NodeStatus.SUPERSEDED)
        view = node_to_view(t, "g")
        assert view.status == "superseded"

    def test_edge_to_view(self):
        e = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS, weight=0.8)
        view = edge_to_view(e, "my-graph")
        assert view.edge_id == e.id
        assert view.src_id == "a"
        assert view.dst_id == "b"
        assert view.edge_type == "supports"
        assert view.weight == 0.8
        assert view.graph == "my-graph"


# --- Per-source validity in the snapshot ---


def _sourced_edge(**kwargs) -> NodeEdge:
    return NodeEdge(src_id="fact-1", dst_id="doc-1", type=EdgeType.SOURCED_FROM, **kwargs)


class TestValidityReachesTheSnapshot:
    """What a source asserts about when a claim was true, as the panel gets it.

    Validity rides on the `sourced_from` edge naming the source, which is where
    the validity model put it: an interval is one source's assertion, and a
    node-level set would have to combine what several sources say into a period
    none of them claims. The snapshot relays the intervals one per source,
    exactly as stored.
    """

    async def test_a_sourced_from_edge_carries_every_interval_its_source_asserts(self, storage):
        """One source, two disjoint periods, both arriving whole.

        A party in government over two separate spans is one claim, so the list
        is the unit rather than the interval, and the view has to keep both.
        """
        edge = _sourced_edge(
            validity=[
                ValidityInterval(
                    start=PreciseInstant(at=datetime(1924, 1, 22, tzinfo=UTC)),
                    end=PreciseInstant(at=datetime(1924, 11, 4, tzinfo=UTC)),
                    basis=IntervalBasis.STATED,
                    witnessed_at=PreciseInstant(at=datetime(1924, 6, 1, tzinfo=UTC)),
                    timeline_id="westminster",
                ),
                ValidityInterval(
                    start=NamedInstant(label="the second Baldwin government"),
                    end=UnknownInstant(),
                    basis=IntervalBasis.INFERRED,
                ),
            ]
        )
        await storage.store_edge(edge)

        data = await assemble_snapshot(storage, "default")

        [stored] = data["edges"]
        first, second = stored["validity"]
        assert first["start"]["at"] == "1924-01-22T00:00:00Z"
        assert first["end"]["at"] == "1924-11-04T00:00:00Z"
        assert first["witnessed_at"]["at"] == "1924-06-01T00:00:00Z"
        assert first["timeline_id"] == "westminster"
        assert first["basis"] == "stated"
        assert second["start"]["label"] == "the second Baldwin government"
        assert second["basis"] == "inferred"
        assert second["timeline_id"] is None
        assert second["witnessed_at"] is None

    async def test_the_endpoint_kinds_arrive_by_name(self, storage):
        """The panel reads which sort of endpoint it has from the payload.

        A located boundary, a boundary the source only named, a boundary whose
        place is unknown and no boundary at all are four different claims, and
        the marks for them differ. Collapsing any pair here would leave the
        panel drawing a date the source never gave.
        """
        edge = _sourced_edge(
            validity=[
                ValidityInterval(
                    start=PreciseInstant(
                        at=datetime(1703, 5, 27, tzinfo=UTC), label="its founding"
                    ),
                    end=UnboundedInstant(),
                    basis=IntervalBasis.STATED,
                ),
                ValidityInterval(
                    start=NamedInstant(label="the Renaissance"),
                    end=UnknownInstant(),
                    basis=IntervalBasis.INFERRED,
                ),
            ]
        )
        await storage.store_edge(edge)

        data = await assemble_snapshot(storage, "default")

        [stored] = data["edges"]
        kinds = [
            (interval["start"]["instant_kind"], interval["end"]["instant_kind"])
            for interval in stored["validity"]
        ]
        assert kinds == [("precise", "unbounded"), ("named", "unknown")]
        assert stored["validity"][0]["start"]["label"] == "its founding"

    async def test_an_edge_with_no_source_to_assert_anything_carries_an_empty_list(self, storage):
        """Only a provenance edge can hold validity, so every other edge shows
        an empty list rather than a missing key."""
        await storage.store_edge(
            NodeEdge(src_id="fact-1", dst_id="topic-1", type=EdgeType.TAGGED_WITH_TOPIC)
        )

        data = await assemble_snapshot(storage, "default")

        [stored] = data["edges"]
        assert stored["validity"] == []

    async def test_the_live_edge_event_carries_the_intervals_too(self):
        """A browser open while the edge is written sees what a reload would
        show it, so the event view and the snapshot view stay one shape."""
        bus = create_event_bus()
        wrapped = instrument_storage(InMemoryStorage(), bus)
        received: list[EdgeStored] = []
        bus.subscribe(EdgeStored, handler=lambda e: received.append(e))

        await wrapped.store_edge(
            _sourced_edge(
                validity=[
                    ValidityInterval(
                        start=PreciseInstant(at=datetime(1991, 9, 6, tzinfo=UTC)),
                        end=UnboundedInstant(),
                        basis=IntervalBasis.STATED,
                    )
                ]
            )
        )

        [event] = received
        [interval] = event.edge.validity
        assert interval.basis == IntervalBasis.STATED
        assert event.edge.model_dump(mode="json")["validity"][0]["start"]["at"] == (
            "1991-09-06T00:00:00Z"
        )

    async def test_a_retired_provenance_edge_stays_out_of_the_snapshot(self, storage):
        """Retirement withdraws the edge, and its intervals go with it: they are
        what that source asserted through an edge nobody follows any more."""
        await storage.store_edge(
            _sourced_edge(
                retired_at=datetime(2024, 3, 1, tzinfo=UTC),
                validity=[
                    ValidityInterval(
                        start=PreciseInstant(at=datetime(1917, 11, 7, tzinfo=UTC)),
                        end=UnknownInstant(),
                        basis=IntervalBasis.STATED,
                    )
                ],
            )
        )

        data = await assemble_snapshot(storage, "default")

        assert data["edges"] == []


# --- Boundaries reflect would propose, worked out for the snapshot ---


@pytest.fixture(params=["memory", "surrealdb"])
async def either_backend(request):
    """Both stores, for the reads the proposals add.

    The rest of this module is in-memory, because it is checking the shape of a
    payload. This part reads nodes by status and edges by type on the way to a
    derived answer, which is exactly where the two backends have diverged
    before.
    """
    if request.param == "memory":
        yield InMemoryStorage()
    else:
        store = SurrealDBStorage(url="mem://")
        await store.connect()
        yield store
        await store.close()


async def _renaming(storage):
    """The worked case: an open period, and a successor that names its date."""
    leningrad = Fact(content="the city is called Leningrad", source_id="seg-1")
    petersburg = Fact(content="the city is called Saint Petersburg", source_id="seg-1")
    for fact in (leningrad, petersburg):
        await storage.store_node(fact)
    await storage.store_edge(
        NodeEdge(
            src_id=leningrad.id,
            dst_id="doc-1970",
            type=EdgeType.SOURCED_FROM,
            validity=[
                ValidityInterval(
                    start=PreciseInstant(at=datetime(1924, 1, 26, tzinfo=UTC)),
                    end=UnknownInstant(),
                    basis=IntervalBasis.STATED,
                )
            ],
        )
    )
    await storage.store_edge(
        NodeEdge(
            src_id=petersburg.id,
            dst_id="doc-2000",
            type=EdgeType.SOURCED_FROM,
            validity=[
                ValidityInterval(
                    start=PreciseInstant(at=datetime(1991, 9, 6, tzinfo=UTC)),
                    end=UnknownInstant(),
                    basis=IntervalBasis.STATED,
                )
            ],
        )
    )
    await storage.set_node_status_tx(
        [leningrad], status=NodeStatus.HISTORICAL, at=datetime(2001, 1, 1, tzinfo=UTC)
    )
    await storage.store_edge(
        NodeEdge(
            src_id=leningrad.id,
            dst_id=petersburg.id,
            type=EdgeType.TEMPORALLY_FOLLOWED_BY,
        )
    )
    return leningrad, petersburg


class TestBoundaryProposalsRideAlong:
    """Where reflect would offer a date, worked out fresh for every snapshot.

    Derived rather than stored: a stored answer would be a photograph of a
    derivation that goes stale the moment either claim changes. The panel draws
    what is here beside the periods from the same reads, so the two describe one
    instant.

    Each backend opens on a graph of its own name, so the tests ask the store
    which one it is on rather than naming it.
    """

    async def test_the_key_is_there_when_there_is_nothing_to_propose(self, either_backend):
        data = await assemble_snapshot(either_backend, either_backend.current_database)

        assert data["boundary_proposals"] == []

    async def test_a_succession_with_no_date_anywhere_proposes_nothing(self, either_backend):
        earlier = Fact(content="a claim", source_id="seg-1")
        later = Fact(content="the next claim", source_id="seg-1")
        for fact in (earlier, later):
            await either_backend.store_node(fact)
        await either_backend.store_edge(
            NodeEdge(src_id=earlier.id, dst_id=later.id, type=EdgeType.TEMPORALLY_FOLLOWED_BY)
        )

        data = await assemble_snapshot(either_backend, either_backend.current_database)

        assert data["boundary_proposals"] == []

    async def test_a_dated_successor_closes_the_claim_it_followed(self, either_backend):
        graph = either_backend.current_database
        leningrad, petersburg = await _renaming(either_backend)

        data = await assemble_snapshot(either_backend, graph)

        [proposal] = data["boundary_proposals"]
        assert proposal["node_id"] == leningrad.id
        assert proposal["source_id"] == "doc-1970"
        assert proposal["endpoint"] == "end"
        assert proposal["at"].startswith("1991-09-06")
        assert proposal["timeline_id"] is None
        # The evidence: the claim the date was read from, and the source dating it.
        assert proposal["because_id"] == petersburg.id
        assert proposal["because_source_id"] == "doc-2000"
        assert proposal["graph"] == graph

    async def test_the_claim_it_touches_is_still_open_in_the_snapshot(self, either_backend):
        """A proposal is an offer, so the periods beside it are untouched."""
        graph = either_backend.current_database
        leningrad, _ = await _renaming(either_backend)

        data = await assemble_snapshot(either_backend, graph)

        [edge] = [
            e
            for e in data["edges"]
            if e["src_id"] == leningrad.id and e["edge_type"] == "sourced_from"
        ]
        assert edge["validity"][0]["end"]["instant_kind"] == "unknown"

    async def test_it_describes_the_graph_asked_for_rather_than_the_active_one(
        self, either_backend
    ):
        """The dashboard asks one session for any graph it can see."""
        home = either_backend.current_database
        await either_backend.switch_database("other")
        leningrad, _ = await _renaming(either_backend)
        await either_backend.switch_database(home)

        data = await assemble_snapshot(either_backend, "other")

        [proposal] = data["boundary_proposals"]
        assert proposal["node_id"] == leningrad.id
        assert proposal["graph"] == "other"
        assert either_backend.current_database == home
        assert (await assemble_snapshot(either_backend, home))["boundary_proposals"] == []
