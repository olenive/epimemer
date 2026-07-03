"""Tests for InstrumentedStorage multi-graph pass-through and events."""

import pytest

from epimemer.core.types import NodeStatus, Topic
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization import instrumented_storage as instrumented_storage_mod
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import GraphSwitched, NodeStored, node_to_view
from epimemer.visualization.instrumented_storage import instrument_storage


@pytest.fixture
def bus():
    return create_event_bus()


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
