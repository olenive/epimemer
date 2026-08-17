"""Tests for InstrumentedStorage multi-graph pass-through and events."""

import pytest

from datetime import datetime, timezone

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    NodeEdge,
    NodeStatus,
    Timeline,
    Topic,
)
from epimemer.pipelines.timeline.functions import add_timepoint
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization import instrumented_storage as instrumented_storage_mod
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import (
    GraphSwitched,
    NodeStatusChanged,
    NodeStored,
    TimelineStored,
    node_to_view,
)
from epimemer.visualization.instrumented_storage import instrument_storage


@pytest.fixture
def bus():
    return create_event_bus()


class TestLifecyclePassThrough:
    """The server calls connect/close unconditionally, so the wrapper must
    forward them to the inner backend (no hasattr guard, no __getattr__)."""

    async def test_connect_close_delegate_to_inner(self, bus):
        calls: list[str] = []

        class SpyStorage(InMemoryStorage):
            async def connect(self) -> None:
                calls.append("connect")

            async def close(self) -> None:
                calls.append("close")

        wrapped = instrument_storage(SpyStorage(), bus)
        await wrapped.connect()
        await wrapped.close()
        assert calls == ["connect", "close"]


class TestTagTopicEmission:
    """Regression: tag/entity Topics have source_id=None. The write path and the
    viz view model must both tolerate that — a tag Topic must never crash a write.
    """

    def test_node_to_view_allows_none_source_id(self):
        """NodeView must accept a Topic with no segment origin (tag/entity topic).

        Previously NodeView.source_id was a non-nullable str, so converting a tag
        Topic raised ValidationError even though Topic.source_id is str | None.
        """
        tag = Topic(content="ledger-agent", source_id=None, extraction_method="agent:tag")
        view = node_to_view(tag, "test-graph")
        assert view.source_id is None
        assert view.content == "ledger-agent"

    async def test_write_batch_tx_with_untagged_topic_commits_and_emits(self, bus):
        """The real smoke-test scenario: a source_id=None Topic goes through
        write_batch_tx. It must commit and emit a NodeStored, not raise.
        """
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[NodeStored] = []
        bus.subscribe(NodeStored, handler=lambda e: received.append(e))

        tag = Topic(content="smoke-test", source_id=None, extraction_method="agent:tag")
        await wrapped.write_batch_tx(nodes=[tag])

        assert await wrapped.get_node(tag.id) is not None       # committed
        assert len(received) == 1                                # emitted
        assert received[0].node.source_id is None

    async def test_write_batch_tx_survives_emit_failure(self, bus, monkeypatch):
        """A committed write must never be reported as failed because event
        emission threw. Otherwise a retrying caller duplicates every node.

        We force the producer-side view construction to raise (the exact spot the
        original bug lived) and assert the write still commits silently.
        """
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)

        def boom(node, graph):
            raise RuntimeError("viz conversion blew up")

        monkeypatch.setattr(instrumented_storage_mod, "node_to_view", boom)

        node = Topic(content="t", source_id="s1")
        await wrapped.write_batch_tx(nodes=[node])   # must not raise

        assert await wrapped.get_node(node.id) is not None       # still committed


class TestSupersessionCounterpart:
    """#57: a retirement event has to say *by whom*.

    The relation also arrives as an `EdgeStored` a moment later, so a consumer
    could in principle join the two by their adjacency in the stream. That join
    breaks as soon as anything interleaves, which is why the id belongs on the
    event that reports the retirement.
    """

    async def _superseded(self, bus, *, status):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[NodeStatusChanged] = []
        bus.subscribe(NodeStatusChanged, handler=lambda e: received.append(e))

        old = Fact(content="Leningrad is the city's name", source_id="s1")
        new = Fact(content="Saint Petersburg is the city's name", source_id="s1")
        await wrapped.store_node(old)
        await wrapped.supersede_node_tx(
            old, new,
            EmbeddingRecord(item_id=new.id, model_id="test", vector=[1.0, 0.0]),
            NodeEdge(src_id=old.id, dst_id=new.id, type=EdgeType.SUPERSEDED_BY),
            status=status,
            superseded_at=datetime.now(timezone.utc),
        )
        return old, new, received

    async def test_node_status_changed_names_the_superseding_node(self, bus):
        old, new, received = await self._superseded(bus, status=NodeStatus.CORRECTED)

        assert [e.node_id for e in received] == [old.id]
        assert received[0].counterpart == new.id

    async def test_world_change_names_the_node_that_followed_it(self, bus):
        """A world-change and a correction are opposite acts (#53) and both need
        the counterpart — the status says which act it was, the id says with what."""
        old, new, received = await self._superseded(bus, status=NodeStatus.HISTORICAL)

        assert received[0].new_status == NodeStatus.HISTORICAL.value
        assert received[0].counterpart == new.id

    async def test_supersede_by_existing_names_the_existing_node(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[NodeStatusChanged] = []
        bus.subscribe(NodeStatusChanged, handler=lambda e: received.append(e))

        old = Fact(content="the earlier claim", source_id="s1")
        winner = Fact(content="the claim that stands", source_id="s1")
        await wrapped.store_node(old)
        await wrapped.store_node(winner)
        await wrapped.supersede_by_existing_tx(
            old, winner.id,
            NodeEdge(src_id=old.id, dst_id=winner.id, type=EdgeType.SUPERSEDED_BY),
            status=NodeStatus.CORRECTED,
            superseded_at=datetime.now(timezone.utc),
        )

        assert [e.counterpart for e in received] == [winner.id]

    async def test_merge_names_the_target_every_source_went_into(self, bus):
        """Same defect, same shape: without the id, "merged" says a node left the
        active set and not where its content ended up."""
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[NodeStatusChanged] = []
        bus.subscribe(NodeStatusChanged, handler=lambda e: received.append(e))

        sources = [Fact(content=f"duplicate {i}", source_id="s1") for i in range(2)]
        for node in sources:
            await wrapped.store_node(node)
        merged = Fact(content="the one kept", source_id="s1")
        await wrapped.merge_nodes_tx(
            sources, merged,
            EmbeddingRecord(item_id=merged.id, model_id="test", vector=[1.0, 0.0]),
            [NodeEdge(src_id=s.id, dst_id=merged.id, type=EdgeType.MERGED_INTO)
             for s in sources],
            merged_at=datetime.now(timezone.utc),
        )

        assert {e.node_id for e in received} == {s.id for s in sources}
        assert {e.counterpart for e in received} == {merged.id}


class TestMultiGraphPassThrough:
    """Verify InstrumentedStorage correctly passes through multi-graph operations."""

    async def test_wrapping_memory_current_database(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        assert wrapped.current_database == "default"

    async def test_wrapping_memory_list_databases(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        assert await wrapped.list_databases() == ["default"]

    async def test_wrapping_memory_switch_database(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        await wrapped.switch_database("second")
        assert wrapped.current_database == "second"
        assert "second" in await wrapped.list_databases()

    async def test_wrapping_memory_delete_database(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        await wrapped.switch_database("to-delete")
        await wrapped.switch_database("default")
        await wrapped.delete_database("to-delete")
        assert "to-delete" not in await wrapped.list_databases()

    async def test_switch_database_emits_graph_switched(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[GraphSwitched] = []
        bus.subscribe(GraphSwitched, handler=lambda e: received.append(e))

        await wrapped.switch_database("new-graph")

        assert len(received) == 1
        assert received[0].previous_graph == "default"
        assert received[0].new_graph == "new-graph"
        assert received[0].graph == "new-graph"

    async def test_viz_list_nodes_passthrough(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)

        t = Topic(content="Test", source_id="s1")
        await wrapped.store_node(t)

        nodes = await wrapped.viz_list_nodes("default")
        assert len(nodes) == 1
        assert nodes[0].id == t.id

    async def test_viz_list_nodes_cross_graph_does_not_switch(self, bus):
        """Viz reads via InstrumentedStorage don't affect active graph."""
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)

        await wrapped.switch_database("other")
        t = Topic(content="Other", source_id="s1")
        await wrapped.store_node(t)
        await wrapped.switch_database("default")

        nodes = await wrapped.viz_list_nodes("other")
        assert len(nodes) == 1
        assert wrapped.current_database == "default"


class TestTimelineEvents:
    """A timeline panel fed only by the load-time snapshot goes stale the moment
    an agent adds a timepoint, so every timeline write must announce itself."""

    async def test_store_timeline_emits_the_whole_timeline(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[TimelineStored] = []
        bus.subscribe(TimelineStored, handler=lambda e: received.append(e))

        timeline, _ = add_timepoint(
            Timeline(name="History"),
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            label="the beginning",
        )
        await wrapped.store_timeline(timeline)

        assert len(received) == 1
        view = received[0].timeline
        assert view.name == "History"
        assert received[0].graph == "default"
        assert [tp.label for tp in view.timepoints] == ["the beginning"]

    async def test_appending_a_timepoint_emits_the_new_state(self, bus):
        """Adding a timepoint re-stores the timeline, and the event must carry
        the timeline as it now is — a viewer applying the event replaces its
        copy rather than merging, so a stale payload would lose the new point."""
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[TimelineStored] = []
        bus.subscribe(TimelineStored, handler=lambda e: received.append(e))

        timeline = Timeline(name="History")
        await wrapped.store_timeline(timeline)
        extended, _ = add_timepoint(
            timeline, start=datetime(2024, 6, 1, tzinfo=timezone.utc)
        )
        await wrapped.store_timeline(extended)

        assert [len(e.timeline.timepoints) for e in received] == [0, 1]
        assert received[1].timeline.timeline_id == timeline.id

    async def test_vague_timepoints_survive_conversion_without_a_start(self, bus):
        """A label-only timepoint must reach the frontend with start unset. If
        conversion invented a date the panel would plot it on the axis as fact."""
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        received: list[TimelineStored] = []
        bus.subscribe(TimelineStored, handler=lambda e: received.append(e))

        timeline, _ = add_timepoint(
            Timeline(name="History"), label="during the Renaissance"
        )
        await wrapped.store_timeline(timeline)

        [point] = received[0].timeline.timepoints
        assert point.start is None
        assert point.end is None
        assert point.label == "during the Renaissance"

    async def test_viz_list_timelines_passes_through(self, bus):
        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        await wrapped.store_timeline(Timeline(name="History"))

        listed = await wrapped.viz_list_timelines("default")
        assert [t.name for t in listed] == ["History"]


class TestAggregatePassThrough:
    """Read-only aggregate queries must pass through the instrumentation wrapper."""

    async def test_count_nodes_by_type(self, bus):
        from epimemer.core.types import Fact, NodeType

        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        await wrapped.store_node(Topic(content="t", source_id="s1"))
        await wrapped.store_node(Fact(content="f", source_id="s1"))

        counts = await wrapped.count_nodes_by_type()
        assert counts[NodeType.TOPIC] == 1
        assert counts[NodeType.FACT] == 1

    async def test_count_edges_by_type(self, bus):
        from epimemer.core.types import EdgeType, NodeEdge

        inner = InMemoryStorage()
        wrapped = instrument_storage(inner, bus)
        await wrapped.store_edge(
            NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
        )

        counts = await wrapped.count_edges_by_type()
        assert counts[EdgeType.SUPPORTS] == 1


def test_wrapper_implements_full_storage_protocol(bus):
    """Guard: the instrumentation wrapper must expose every StorageBackend method.

    InstrumentedStorage delegates explicitly (no __getattr__ fallback), so a new
    protocol method that isn't wrapped silently breaks at runtime. This fails fast.
    """
    from epimemer.storage.protocol import StorageBackend

    wrapped = instrument_storage(InMemoryStorage(), bus)
    protocol_members = [
        name for name in dir(StorageBackend) if not name.startswith("_")
    ]
    missing = [name for name in protocol_members if not hasattr(wrapped, name)]
    assert missing == [], f"InstrumentedStorage missing protocol members: {missing}"
