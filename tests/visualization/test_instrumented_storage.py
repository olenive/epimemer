"""Tests for InstrumentedStorage multi-graph pass-through and events."""

import pytest

from epimemer.core.types import NodeStatus, Topic
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import GraphSwitched
from epimemer.visualization.instrumented_storage import instrument_storage


@pytest.fixture
def bus():
    return create_event_bus()


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
