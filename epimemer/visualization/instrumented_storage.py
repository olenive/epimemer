"""Storage wrapper that publishes graph events to an EventBus.

Wraps any StorageBackend implementation and emits events on mutations
without modifying the underlying storage logic. Read-only operations
pass through without emitting events.

Usage:
    bus = create_event_bus()
    raw_storage = InMemoryStorage()
    storage = instrument_storage(raw_storage, bus)

    # Now all writes emit events; reads are unchanged.
    await storage.store_node(topic)  # → publishes NodeStored event
"""

from datetime import datetime
from typing import Sequence

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
)
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    DocumentStored,
    EdgeStored,
    EmbeddingStored,
    NodeStatusChanged,
    NodeStored,
    SegmentStored,
)


def _node_type_label(node: EpistemicNode) -> str:
    match node:
        case Topic():
            return "topic"
        case Fact():
            return "fact"
        case Inference():
            return "inference"
        case _:
            return "unknown"


class InstrumentedStorage:
    """Wraps a StorageBackend and publishes graph mutation events.

    All write methods publish events after the underlying operation succeeds.
    All read methods delegate directly with no overhead.
    """

    def __init__(self, inner: object, bus: InProcessEventBus) -> None:
        self._inner = inner
        self._bus = bus

    # --- Documents (write) ---

    async def store_document(self, doc: RawDocument) -> str:
        result = await self._inner.store_document(doc)
        await self._bus.publish(DocumentStored(
            document_id=doc.id,
            content_preview=doc.content[:200],
            metadata=doc.metadata,
        ))
        return result

    # --- Documents (read) ---

    async def get_document(self, doc_id: str) -> RawDocument | None:
        return await self._inner.get_document(doc_id)

    # --- Segments (write) ---

    async def store_segment(self, segment: Segment) -> str:
        result = await self._inner.store_segment(segment)
        await self._bus.publish(SegmentStored(
            segment_id=segment.id,
            source_id=segment.source_id,
            text_preview=segment.text[:200],
            span_start=segment.span_start,
            span_end=segment.span_end,
        ))
        return result

    # --- Segments (read) ---

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        return await self._inner.get_segments_for_document(doc_id)

    # --- Epistemic Nodes (write) ---

    async def store_node(self, node: EpistemicNode) -> str:
        result = await self._inner.store_node(node)
        await self._bus.publish(NodeStored(
            node_id=node.id,
            node_type=_node_type_label(node),
            content=node.content,
            status=node.status.value,
            metadata=node.metadata,
        ))
        return result

    async def update_node_status(
        self,
        node_id: str,
        status: NodeStatus,
        superseded_at: datetime | None = None,
    ) -> None:
        # Fetch old status before the update
        node = await self._inner.get_node(node_id)
        old_status = node.status.value if node else "unknown"

        await self._inner.update_node_status(node_id, status, superseded_at)

        await self._bus.publish(NodeStatusChanged(
            node_id=node_id,
            old_status=old_status,
            new_status=status.value,
        ))

    # --- Epistemic Nodes (read) ---

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        return await self._inner.get_node(node_id)

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
        at_time: datetime | None = None,
    ) -> Sequence[EpistemicNode]:
        return await self._inner.query_nodes(
            node_type=node_type, status=status, at_time=at_time,
        )

    # --- Edges (write) ---

    async def store_edge(self, edge: NodeEdge) -> str:
        result = await self._inner.store_edge(edge)
        await self._bus.publish(EdgeStored(
            edge_id=edge.id,
            src_id=edge.src_id,
            dst_id=edge.dst_id,
            edge_type=edge.type.value,
            weight=edge.weight,
            metadata=edge.metadata,
        ))
        return result

    # --- Edges (read) ---

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return await self._inner.get_edges_from(node_id, edge_type=edge_type)

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return await self._inner.get_edges_to(node_id, edge_type=edge_type)

    # --- Embeddings (write) ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        result = await self._inner.store_embedding(embedding)
        await self._bus.publish(EmbeddingStored(
            item_id=embedding.item_id,
            model_id=embedding.model_id,
            dimensions=len(embedding.vector),
        ))
        return result

    # --- Embeddings (read) ---

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        return await self._inner.get_embeddings_for_item(item_id, model_id)

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
    ) -> Sequence[tuple[str, float]]:
        return await self._inner.vector_search(
            query_vector, model_id, k=k, node_type=node_type,
        )

    # --- Timelines (pass-through) ---

    async def store_timeline(self, timeline: Timeline) -> str:
        return await self._inner.store_timeline(timeline)

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        return await self._inner.get_timeline(timeline_id)

    async def query_timelines(self) -> Sequence[Timeline]:
        return await self._inner.query_timelines()

    # --- Metacontexts (pass-through) ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        return await self._inner.store_metacontext(mc)

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        return await self._inner.get_metacontext(mc_id)

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        return await self._inner.query_metacontexts(status=status)


    # --- Multi-graph management (pass-through) ---

    @property
    def supports_multi_graph(self) -> bool:
        return self._inner.supports_multi_graph

    @property
    def current_database(self) -> str:
        return self._inner.current_database

    async def list_databases(self) -> list[str]:
        return await self._inner.list_databases()

    async def switch_database(self, database: str) -> None:
        await self._inner.switch_database(database)

    async def delete_database(self, database: str) -> None:
        await self._inner.delete_database(database)


def instrument_storage(inner: object, bus: InProcessEventBus) -> InstrumentedStorage:
    """Wrap a storage backend with event instrumentation.

    The returned object satisfies the StorageBackend protocol and
    publishes graph events on every write operation.
    """
    return InstrumentedStorage(inner, bus)
