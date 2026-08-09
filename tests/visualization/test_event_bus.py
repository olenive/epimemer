"""Tests for the event bus and instrumented storage."""

from datetime import datetime, timezone

import pytest

from epimemer.core.types import (
    Fact,
    NodeEdge,
    EdgeType,
    EmbeddingRecord,
    RawDocument,
    Segment,
    Topic,
)
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.events import (
    DocumentStored,
    EdgeStored,
    EdgeView,
    EmbeddingStored,
    Event,
    EventCategory,
    GraphSwitched,
    NodeStatusChanged,
    NodeStored,
    NodeView,
    SegmentStored,
)
from epimemer.visualization.instrumented_storage import instrument_storage


def _make_node_view(**overrides) -> NodeView:
    """Helper to create a NodeView with sensible defaults."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        node_id="n1", node_type="topic", content="test", status="active",
        source_id="seg1", extraction_method="agent", novelty=1.0, confidence=0.5,
        relevance=0.5, retrieved_at=now, created_at=now, graph="default",
    )
    defaults.update(overrides)
    return NodeView(**defaults)


def _make_edge_view(**overrides) -> EdgeView:
    """Helper to create an EdgeView with sensible defaults."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        edge_id="e1", src_id="a", dst_id="b", edge_type="supports",
        weight=1.0, created_at=now, graph="default",
    )
    defaults.update(overrides)
    return EdgeView(**defaults)


@pytest.fixture
def bus():
    return create_event_bus()


@pytest.fixture
def storage(bus):
    return instrument_storage(InMemoryStorage(), bus)


# --- EventBus tests ---


@pytest.mark.asyncio
async def test_publish_and_subscribe(bus):
    received: list[Event] = []
    bus.subscribe(handler=lambda e: received.append(e))

    event = NodeStored(node=_make_node_view())
    await bus.publish(event)

    assert len(received) == 1
    assert received[0].node.node_id == "n1"


@pytest.mark.asyncio
async def test_filter_by_event_type(bus):
    nodes: list[Event] = []
    edges: list[Event] = []

    bus.subscribe(NodeStored, handler=lambda e: nodes.append(e))
    bus.subscribe(EdgeStored, handler=lambda e: edges.append(e))

    await bus.publish(NodeStored(node=_make_node_view()))
    await bus.publish(EdgeStored(edge=_make_edge_view()))

    assert len(nodes) == 1
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_filter_by_category(bus):
    graph_events: list[Event] = []
    bus.subscribe(category=EventCategory.GRAPH, handler=lambda e: graph_events.append(e))

    await bus.publish(NodeStored(node=_make_node_view()))

    assert len(graph_events) == 1


@pytest.mark.asyncio
async def test_unsubscribe(bus):
    received: list[Event] = []
    unsub = bus.subscribe(handler=lambda e: received.append(e))

    await bus.publish(NodeStored(node=_make_node_view(node_id="n1", content="a")))
    unsub()
    await bus.publish(NodeStored(node=_make_node_view(node_id="n2", content="b")))

    assert len(received) == 1


@pytest.mark.asyncio
async def test_handler_error_does_not_propagate(bus):
    """A failing handler should not break other handlers or the publisher."""
    received: list[Event] = []

    async def bad_handler(event: Event) -> None:
        raise RuntimeError("boom")

    bus.subscribe(handler=bad_handler)
    bus.subscribe(handler=lambda e: received.append(e))

    # Should not raise
    await bus.publish(NodeStored(node=_make_node_view()))

    assert len(received) == 1


# --- InstrumentedStorage tests ---


@pytest.mark.asyncio
async def test_store_node_emits_event(bus, storage):
    received: list[NodeStored] = []
    bus.subscribe(NodeStored, handler=lambda e: received.append(e))

    topic = Topic(content="Machine learning", source_id="seg1")
    await storage.store_node(topic)

    assert len(received) == 1
    assert received[0].node.node_id == topic.id
    assert received[0].node.node_type == "topic"
    assert received[0].node.content == "Machine learning"


@pytest.mark.asyncio
async def test_store_edge_emits_event(bus, storage):
    received: list[EdgeStored] = []
    bus.subscribe(EdgeStored, handler=lambda e: received.append(e))

    edge = NodeEdge(src_id="a", dst_id="b", type=EdgeType.SUPPORTS)
    await storage.store_edge(edge)

    assert len(received) == 1
    assert received[0].edge.src_id == "a"
    assert received[0].edge.dst_id == "b"
    assert received[0].edge.edge_type == "supports"


@pytest.mark.asyncio
async def test_store_document_emits_event(bus, storage):
    received: list[DocumentStored] = []
    bus.subscribe(DocumentStored, handler=lambda e: received.append(e))

    doc = RawDocument(content="Some long text about something.")
    await storage.store_document(doc)

    assert len(received) == 1
    assert received[0].document_id == doc.id


@pytest.mark.asyncio
async def test_store_segment_emits_event(bus, storage):
    received: list[SegmentStored] = []
    bus.subscribe(SegmentStored, handler=lambda e: received.append(e))

    seg = Segment(source_id="doc1", text="paragraph text", span_start=0, span_end=14)
    await storage.store_segment(seg)

    assert len(received) == 1
    assert received[0].segment_id == seg.id


@pytest.mark.asyncio
async def test_store_embedding_emits_event(bus, storage):
    received: list[EmbeddingStored] = []
    bus.subscribe(EmbeddingStored, handler=lambda e: received.append(e))

    emb = EmbeddingRecord(item_id="n1", model_id="test-model", vector=[0.1, 0.2, 0.3])
    await storage.store_embedding(emb)

    assert len(received) == 1
    assert received[0].item_id == "n1"
    assert received[0].dimensions == 3


@pytest.mark.asyncio
async def test_node_status_change_emits_event(bus, storage):
    received: list[NodeStatusChanged] = []
    bus.subscribe(NodeStatusChanged, handler=lambda e: received.append(e))

    topic = Topic(content="Test topic", source_id="seg1")
    await storage.store_node(topic)

    from epimemer.core.types import NodeStatus
    await storage.update_node_status(topic.id, NodeStatus.SUPERSEDED)

    assert len(received) == 1
    assert received[0].old_status == "active"
    assert received[0].new_status == "superseded"


@pytest.mark.asyncio
async def test_read_operations_do_not_emit(bus, storage):
    received: list[Event] = []
    bus.subscribe(handler=lambda e: received.append(e))

    topic = Topic(content="Test topic", source_id="seg1")
    await storage.store_node(topic)
    event_count_after_write = len(received)

    # Read operations should not add events
    await storage.get_node(topic.id)
    await storage.query_nodes()

    assert len(received) == event_count_after_write


@pytest.mark.asyncio
async def test_instrumented_storage_passthrough(bus, storage):
    """Verify that reads return the same data the inner storage holds."""
    topic = Topic(content="Passthrough test", source_id="seg1")
    await storage.store_node(topic)

    fetched = await storage.get_node(topic.id)
    assert fetched is not None
    assert fetched.content == "Passthrough test"


@pytest.mark.asyncio
async def test_switch_database_emits_graph_switched(bus, storage):
    received: list[GraphSwitched] = []
    bus.subscribe(GraphSwitched, handler=lambda e: received.append(e))

    await storage.switch_database("new-graph")

    assert len(received) == 1
    assert received[0].previous_graph == "default"
    assert received[0].new_graph == "new-graph"
    assert received[0].graph == "new-graph"
