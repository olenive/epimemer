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

import logging
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
from epimemer.storage.protocol import EdgeDirection, resolve_reflect_threshold
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    DocumentStored,
    EdgeStored,
    EmbeddingStored,
    GraphSwitched,
    NodeStatusChanged,
    NodeStored,
    ReflectCounterUpdated,
    SegmentStored,
    TimelineStored,
    edge_to_view,
    node_to_view,
    timeline_to_view,
)

logger = logging.getLogger(__name__)


class InstrumentedStorage:
    """Wraps a StorageBackend and publishes graph mutation events.

    All write methods publish events after the underlying operation succeeds.
    All read methods delegate directly with no overhead.
    """

    def __init__(
        self, inner: object, bus: InProcessEventBus, default_threshold: int
    ) -> None:
        self._inner = inner
        self._bus = bus
        # The process default the reflect counter is judged against. Stored here
        # because a per-graph override can replace it but not supply it, and the
        # wrapper has no access to server config otherwise.
        self._default_threshold = default_threshold

    # --- Lifecycle (delegate) ---

    async def connect(self) -> None:
        await self._inner.connect()

    async def close(self) -> None:
        await self._inner.close()

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

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        return await self._inner.get_document_by_source(source)

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

    async def relabel_edges(self, old_label: str, new_label: str) -> int:
        return await self._inner.relabel_edges(old_label, new_label)

    async def get_relation_kind(self, label: str) -> str | None:
        return await self._inner.get_relation_kind(label)

    # --- Epistemic Nodes (read) ---

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        return await self._inner.get_node(node_id)

    async def get_nodes(self, node_ids: Sequence[str]) -> dict[str, EpistemicNode]:
        return await self._inner.get_nodes(node_ids)

    async def get_node_by_content(
        self,
        content: str,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> EpistemicNode | None:
        return await self._inner.get_node_by_content(
            content, node_type=node_type, status=status,
        )

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

    async def query_changes(
        self,
        *,
        start: datetime,
        end: datetime,
        node_type: NodeType | None = None,
    ) -> Sequence[EpistemicNode]:
        return await self._inner.query_changes(
            start=start, end=end, node_type=node_type,
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
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
    ) -> None:
        await self._inner.supersede_node_tx(
            old_node, new_node, new_embedding, lineage_edge,
            superseded_at=superseded_at,
            evidence_edges=evidence_edges,
            clear_edge_ids=clear_edge_ids,
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
        for edge in evidence_edges:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))

    async def supersede_by_existing_tx(
        self,
        old_node: EpistemicNode,
        existing_id: str,
        lineage_edge: NodeEdge,
        *,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
    ) -> None:
        await self._inner.supersede_by_existing_tx(
            old_node, existing_id, lineage_edge,
            superseded_at=superseded_at,
            evidence_edges=evidence_edges,
            clear_edge_ids=clear_edge_ids,
        )
        graph = self._inner.current_database
        await self._bus.publish(NodeStatusChanged(
            graph=graph,
            node_id=old_node.id,
            old_status=old_node.status.value,
            new_status=NodeStatus.SUPERSEDED.value,
        ))
        await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(lineage_edge, graph)))
        for edge in evidence_edges:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        retired_at: datetime | None,
    ) -> None:
        await self._inner.set_node_status_tx(
            nodes, status=status, retired_at=retired_at
        )
        graph = self._inner.current_database
        for node in nodes:
            await self._bus.publish(NodeStatusChanged(
                graph=graph,
                node_id=node.id,
                old_status=node.status.value,
                new_status=status.value,
            ))

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

    async def write_batch_tx(
        self,
        *,
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
        timelines: Sequence[Timeline] = (),
    ) -> None:
        await self._inner.write_batch_tx(
            nodes=nodes, edges=edges, embeddings=embeddings, timelines=timelines
        )
        # The write above is already committed. Event emission is best-effort
        # observability and must never turn a committed write into a reported
        # failure — otherwise a retrying caller duplicates every node.
        try:
            graph = self._inner.current_database
            for node in nodes:
                await self._bus.publish(NodeStored(graph=graph, node=node_to_view(node, graph)))
            for edge in edges:
                await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))
            for embedding in embeddings:
                await self._bus.publish(EmbeddingStored(
                    item_id=embedding.item_id,
                    model_id=embedding.model_id,
                    dimensions=len(embedding.vector),
                ))
            for timeline in timelines:
                await self._bus.publish(TimelineStored(
                    graph=graph, timeline=timeline_to_view(timeline, graph),
                ))
        except Exception:
            logger.exception("write_batch_tx event emission failed; write already committed")

    # --- Edges (read) ---

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return await self._inner.get_edges_from(node_id, edge_type=edge_type)

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return await self._inner.get_edges_to(node_id, edge_type=edge_type)

    async def get_edges_for(
        self,
        node_ids: Sequence[str],
        *,
        direction: EdgeDirection,
        edge_type: EdgeType | None = None,
    ) -> dict[str, list[NodeEdge]]:
        return await self._inner.get_edges_for(
            node_ids, direction=direction, edge_type=edge_type
        )

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

    async def get_embeddings_for_items(
        self, item_ids: Sequence[str], *, model_id: str | None = None
    ) -> dict[str, list[EmbeddingRecord]]:
        return await self._inner.get_embeddings_for_items(item_ids, model_id=model_id)

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

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        timeline_id = await self._inner.store_timeline(timeline)
        await self._bus.publish(TimelineStored(
            graph=self._inner.current_database,
            timeline=timeline_to_view(timeline, self._inner.current_database),
        ))
        return timeline_id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        return await self._inner.get_timeline(timeline_id)

    async def query_timelines(self) -> Sequence[Timeline]:
        return await self._inner.query_timelines()

    async def viz_list_timelines(self, database: str) -> Sequence[Timeline]:
        return await self._inner.viz_list_timelines(database)

    async def viz_list_metacontexts(self, database: str) -> Sequence[Metacontext]:
        return await self._inner.viz_list_metacontexts(database)

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

    # --- Reflection bookkeeping (pass-through) ---

    async def _publish_reflect_state(self, count: int) -> None:
        """Announce reflection pressure for the active graph.

        Emitted on every write that can move either number — both counter
        mutations and a threshold change — because a viewer updated on stores
        alone would show the right count against a stale denominator.
        """
        threshold = resolve_reflect_threshold(
            await self._inner.get_reflect_threshold_override(),
            self._default_threshold,
        )
        await self._bus.publish(ReflectCounterUpdated(
            graph=self._inner.current_database,
            count=count,
            threshold=threshold,
            suggested=count >= threshold,
        ))

    async def get_reflect_counter(self) -> int:
        return await self._inner.get_reflect_counter()

    async def bump_reflect_counter(self) -> int:
        count = await self._inner.bump_reflect_counter()
        await self._publish_reflect_state(count)
        return count

    async def reset_reflect_counter(self) -> int:
        previous = await self._inner.reset_reflect_counter()
        await self._publish_reflect_state(0)
        return previous

    async def get_reflect_threshold_override(self) -> int | None:
        return await self._inner.get_reflect_threshold_override()

    async def set_reflect_threshold_override(self, threshold: int | None) -> None:
        await self._inner.set_reflect_threshold_override(threshold)
        await self._publish_reflect_state(await self._inner.get_reflect_counter())

    # --- Multi-graph management (pass-through) ---

    @property
    def backend_name(self) -> str:
        return self._inner.backend_name

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


def instrument_storage(
    inner: object, bus: InProcessEventBus, default_threshold: int = 10
) -> InstrumentedStorage:
    """Wrap a storage backend with event instrumentation.

    The returned object satisfies the StorageBackend protocol and
    publishes graph events on every write operation.

    `default_threshold` is the server's configured reflect threshold, reported
    on reflection-pressure events unless the graph overrides it. It defaults to
    the same value as `ServerConfig.reflect_threshold` so tests and ad-hoc
    wrapping need not supply it; the MCP lifespan passes the real config.
    """
    return InstrumentedStorage(inner, bus, default_threshold)
