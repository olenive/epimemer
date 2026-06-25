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
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
)
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    DocumentStored,
    EdgeStored,
    EmbeddingStored,
    GraphSwitched,
    NodeStatusChanged,
    NodeStored,
    SegmentStored,
    edge_to_view,
    node_to_view,
)


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
        graph = self._inner.current_database
        await self._bus.publish(NodeStored(
            graph=graph,
            node=node_to_view(node, graph),
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
            graph=self._inner.current_database,
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

    async def count_nodes_by_type(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> dict[NodeType, int]:
        return await self._inner.count_nodes_by_type(status=status)

    # --- Edges (write) ---

    async def store_edge(self, edge: NodeEdge) -> str:
        result = await self._inner.store_edge(edge)
        graph = self._inner.current_database
        await self._bus.publish(EdgeStored(
            graph=graph,
            edge=edge_to_view(edge, graph),
        ))
        return result

    async def delete_edge(self, edge_id: str) -> None:
        await self._inner.delete_edge(edge_id)

    # --- Atomic compound operations (write) ---

    async def supersede_node_tx(
        self,
        old_node: EpistemicNode,
        new_node: EpistemicNode,
        new_embedding: EmbeddingRecord,
        lineage_edge: NodeEdge,
        *,
        superseded_at: datetime,
    ) -> None:
        await self._inner.supersede_node_tx(
            old_node, new_node, new_embedding, lineage_edge,
            superseded_at=superseded_at,
        )
        # Publish only after the atomic operation succeeds.
        graph = self._inner.current_database
        await self._bus.publish(NodeStatusChanged(
            graph=graph,
            node_id=old_node.id,
            old_status=old_node.status.value,
            new_status=NodeStatus.SUPERSEDED.value,
        ))
        await self._bus.publish(NodeStored(graph=graph, node=node_to_view(new_node, graph)))
        await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(lineage_edge, graph)))

    async def merge_nodes_tx(
        self,
        source_nodes: Sequence[EpistemicNode],
        merged_node: EpistemicNode,
        merged_embedding: EmbeddingRecord,
        lineage_edges: Sequence[NodeEdge],
        *,
        merged_at: datetime,
    ) -> None:
        await self._inner.merge_nodes_tx(
            source_nodes, merged_node, merged_embedding, lineage_edges,
            merged_at=merged_at,
        )
        graph = self._inner.current_database
        await self._bus.publish(NodeStored(graph=graph, node=node_to_view(merged_node, graph)))
        for source in source_nodes:
            await self._bus.publish(NodeStatusChanged(
                graph=graph,
                node_id=source.id,
                old_status=source.status.value,
                new_status=NodeStatus.MERGED.value,
            ))
        for edge in lineage_edges:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))

    # --- Edges (read) ---

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return await self._inner.get_edges_from(node_id, edge_type=edge_type)

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return await self._inner.get_edges_to(node_id, edge_type=edge_type)

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        return await self._inner.count_edges_by_type()

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
    def current_database(self) -> str:
        return self._inner.current_database

    async def list_databases(self) -> list[str]:
        return await self._inner.list_databases()

    async def switch_database(self, database: str) -> None:
        previous = self._inner.current_database
        await self._inner.switch_database(database)
        await self._bus.publish(GraphSwitched(
            graph=database,
            previous_graph=previous,
            new_graph=database,
        ))

    async def delete_database(self, database: str) -> None:
        await self._inner.delete_database(database)

    # --- Viz reads (pass-through, no events emitted) ---

    async def viz_list_nodes(
        self,
        database: str,
        *,
        historical_status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[EpistemicNode]:
        return await self._inner.viz_list_nodes(
            database, historical_status=historical_status,
        )

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        return await self._inner.viz_list_edges(database)


def instrument_storage(inner: object, bus: InProcessEventBus) -> InstrumentedStorage:
    """Wrap a storage backend with event instrumentation.

    The returned object satisfies the StorageBackend protocol and
    publishes graph events on every write operation.
    """
    return InstrumentedStorage(inner, bus)
