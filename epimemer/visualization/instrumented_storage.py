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
from typing import Literal, Sequence

from epimemer.core.types import (
    Agent,
    DecisionKind,
    DecisionRecord,
    EdgeType,
    JudgeRef,
    EmbeddingRecord,
    EpistemicNode,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    RelationLabel,
    RelationVerdict,
    Segment,
    Timeline,
)
from epimemer.storage.active_graph import GraphGuard
from epimemer.storage.protocol import (
    EdgeDirection,
    MergeOverrides,
    WarningOverrides,
    resolve_reflect_threshold,
)
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import (
    ActionVerb,
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
from epimemer.visualization.graph_actions import graph_action, verb_for_status

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

    async def get_segments(self, segment_ids: Sequence[str]) -> dict[str, Segment]:
        return await self._inner.get_segments(segment_ids)

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

    async def count_nodes_without_frame(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> int:
        return await self._inner.count_nodes_without_frame(status=status)

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
        status: NodeStatus,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        await self._inner.supersede_node_tx(
            old_node, new_node, new_embedding, lineage_edge,
            status=status, superseded_at=superseded_at,
            evidence_edges=evidence_edges,
            clear_edge_ids=clear_edge_ids,
            judge=judge,
        )
        # Publish only after the atomic operation succeeds.
        graph = self._inner.current_database
        await self._bus.publish(NodeStatusChanged(
            graph=graph,
            node_id=old_node.id,
            old_status=old_node.status.value,
            new_status=status.value,
            counterpart=new_node.id,
        ))
        await self._bus.publish(NodeStored(graph=graph, node=node_to_view(new_node, graph)))
        await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(lineage_edge, graph)))
        for edge in evidence_edges:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))
        await self._bus.publish(graph_action(
            graph=graph,
            verb=verb_for_status(status),
            subjects=[old_node.id, new_node.id],
            counts={"nodes": 1, "edges": 1 + len(evidence_edges)},
        ))

    async def supersede_by_existing_tx(
        self,
        old_node: EpistemicNode,
        existing_id: str,
        lineage_edge: NodeEdge,
        *,
        status: NodeStatus,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        await self._inner.supersede_by_existing_tx(
            old_node, existing_id, lineage_edge,
            status=status, superseded_at=superseded_at,
            evidence_edges=evidence_edges,
            clear_edge_ids=clear_edge_ids,
            judge=judge,
        )
        graph = self._inner.current_database
        await self._bus.publish(NodeStatusChanged(
            graph=graph,
            node_id=old_node.id,
            old_status=old_node.status.value,
            new_status=status.value,
            counterpart=existing_id,
        ))
        await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(lineage_edge, graph)))
        for edge in evidence_edges:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))
        await self._bus.publish(graph_action(
            graph=graph,
            verb=verb_for_status(status),
            subjects=[old_node.id, existing_id],
            counts={"edges": 1 + len(evidence_edges)},
        ))

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        at: datetime,
        edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        await self._inner.set_node_status_tx(
            nodes, status=status, at=at, edges=edges, judge=judge
        )
        graph = self._inner.current_database
        for node in nodes:
            await self._bus.publish(NodeStatusChanged(
                graph=graph,
                node_id=node.id,
                old_status=node.status.value,
                new_status=status.value,
            ))
        # Archival flips a batch; restore flips it back. Either way it is one
        # act, whatever it swept up — which is what keeps a 600-node archival
        # run from being 600 log lines.
        if nodes:
            await self._bus.publish(graph_action(
                graph=graph,
                verb=verb_for_status(status),
                subjects=[node.id for node in nodes],
                counts={"nodes": len(nodes)},
            ))

    async def merge_nodes_tx(
        self,
        source_nodes: Sequence[EpistemicNode],
        merged_node: EpistemicNode,
        merged_embedding: EmbeddingRecord,
        lineage_edges: Sequence[NodeEdge],
        *,
        merged_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        await self._inner.merge_nodes_tx(
            source_nodes, merged_node, merged_embedding, lineage_edges,
            merged_at=merged_at, evidence_edges=evidence_edges, judge=judge,
        )
        graph = self._inner.current_database
        await self._bus.publish(NodeStored(graph=graph, node=node_to_view(merged_node, graph)))
        for source in source_nodes:
            await self._bus.publish(NodeStatusChanged(
                graph=graph,
                node_id=source.id,
                old_status=source.status.value,
                new_status=NodeStatus.MERGED.value,
                counterpart=merged_node.id,
            ))
        for edge in [*lineage_edges, *evidence_edges]:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))
        await self._bus.publish(graph_action(
            graph=graph,
            verb=ActionVerb.MERGED,
            # The survivor first: "merged 2 nodes into 5e6f7g8h" is the line, and
            # where the content went is the part worth reading.
            subjects=[merged_node.id, *(s.id for s in source_nodes)],
            counts={"nodes": 1, "edges": len(lineage_edges) + len(evidence_edges)},
        ))

    async def reverse_merge_tx(
        self,
        survivor: EpistemicNode,
        source_nodes: Sequence[EpistemicNode],
        restored_edges: Sequence[NodeEdge],
        *,
        restored_at: datetime,
        delete_edge_ids: Sequence[str],
        judge: JudgeRef | None = None,
    ) -> None:
        await self._inner.reverse_merge_tx(
            survivor, source_nodes, restored_edges,
            restored_at=restored_at, delete_edge_ids=delete_edge_ids, judge=judge,
        )
        graph = self._inner.current_database
        for source in source_nodes:
            await self._bus.publish(NodeStatusChanged(
                graph=graph,
                node_id=source.id,
                old_status=NodeStatus.MERGED.value,
                new_status=NodeStatus.ACTIVE.value,
                counterpart=survivor.id,
            ))
        for edge in restored_edges:
            await self._bus.publish(EdgeStored(graph=graph, edge=edge_to_view(edge, graph)))
        # RESTORED rather than a verb of its own: what a reader needs from the
        # strip is that these nodes are back in the active set. **The survivor's
        # deletion is not published** — there is no node-deleted event and no
        # renderer for one, so a viewer keeps showing it until the next
        # snapshot. Recorded as a known gap rather than half-built here.
        await self._bus.publish(graph_action(
            graph=graph,
            verb=ActionVerb.RESTORED,
            subjects=[source.id for source in source_nodes],
            counts={"nodes": len(source_nodes), "edges": len(restored_edges)},
        ))

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
            await self._bus.publish(graph_action(
                graph=graph,
                verb=ActionVerb.STORED,
                subjects=[node.id for node in nodes],
                counts={
                    "nodes": len(nodes),
                    "edges": len(edges),
                    "embeddings": len(embeddings),
                    "timelines": len(timelines),
                },
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
        statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
    ) -> Sequence[tuple[str, float]]:
        return await self._inner.vector_search(
            query_vector, model_id, k=k, node_type=node_type, statuses=statuses,
        )

    async def text_search(
        self,
        terms: Sequence[str],
        *,
        corpus: Literal["nodes", "segments"],
        k: int = 10,
        node_type: NodeType | None = None,
        statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
        verify_containment: bool = False,
    ) -> Sequence[tuple[str, float]]:
        return await self._inner.text_search(
            terms,
            corpus=corpus,
            k=k,
            node_type=node_type,
            statuses=statuses,
            verify_containment=verify_containment,
        )

    async def get_nodes_by_source(
        self, source_ids: Sequence[str]
    ) -> dict[str, list[EpistemicNode]]:
        return await self._inner.get_nodes_by_source(source_ids)

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

    async def viz_list_relation_labels(
        self, database: str
    ) -> Sequence[RelationLabel]:
        return await self._inner.viz_list_relation_labels(database)

    # --- Metacontexts (pass-through) ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        return await self._inner.store_metacontext(mc)

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        return await self._inner.get_metacontext(mc_id)

    async def store_relation_label(self, label: RelationLabel) -> str:
        return await self._inner.store_relation_label(label)

    async def get_relation_label(
        self, name: str, kind: str
    ) -> RelationLabel | None:
        return await self._inner.get_relation_label(name, kind)

    async def query_relation_labels(self) -> Sequence[RelationLabel]:
        return await self._inner.query_relation_labels()

    async def record_relation_verdict(self, verdict: RelationVerdict) -> str:
        return await self._inner.record_relation_verdict(verdict)

    async def judged_relation_pairs(self) -> set[tuple[str, str]]:
        return await self._inner.judged_relation_pairs()

    async def query_relation_verdicts(self) -> Sequence[RelationVerdict]:
        return await self._inner.query_relation_verdicts()

    async def relation_verdicts_for(
        self, label_ids: Sequence[str]
    ) -> Sequence[RelationVerdict]:
        return await self._inner.relation_verdicts_for(label_ids)

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

    async def get_merge_overrides(self) -> MergeOverrides:
        return await self._inner.get_merge_overrides()

    async def set_merge_overrides(self, overrides: MergeOverrides) -> None:
        # No event: nothing in the strip shows the merge settings, and a badge
        # that never renders is not worth an event type nobody consumes.
        await self._inner.set_merge_overrides(overrides)

    async def get_warning_overrides(self) -> WarningOverrides:
        return await self._inner.get_warning_overrides()

    async def set_warning_overrides(self, overrides: WarningOverrides) -> None:
        # No event, for the same reason as the merge settings above.
        await self._inner.set_warning_overrides(overrides)

    # --- Agents (pass-through) ---
    #
    # No events, deliberately. Who is judging is not graph content, and putting
    # it on the strip would make a self-reported description look like a fact
    # the graph holds — which §2.4 spends its length warning against.

    async def get_agent(self, agent_id: str) -> Agent | None:
        return await self._inner.get_agent(agent_id)

    async def upsert_agent(self, agent: Agent) -> None:
        await self._inner.upsert_agent(agent)

    async def list_agents(self) -> list[Agent]:
        return await self._inner.list_agents()

    async def get_approved_agent_ids(self) -> list[str]:
        return await self._inner.get_approved_agent_ids()

    async def set_approved_agent_ids(self, ids: list[str]) -> None:
        await self._inner.set_approved_agent_ids(ids)

    async def get_require_judge(self) -> bool | None:
        return await self._inner.get_require_judge()

    async def set_require_judge(self, required: bool | None) -> None:
        await self._inner.set_require_judge(required)

    # --- The decision journal (pass-through) ---
    #
    # No events either, and for the agents' reason one step on: a journal row is
    # a statement *about* a change the strip already shows, so emitting it would
    # draw every write twice.

    async def record_decision(self, record: DecisionRecord) -> str:
        return await self._inner.record_decision(record)

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        return await self._inner.get_decision(decision_id)

    async def query_decisions(
        self,
        *,
        agent_ids: Sequence[str] | None = None,
        kinds: Sequence[DecisionKind] | None = None,
        subject_id: str | None = None,
        reviews: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[DecisionRecord]:
        return await self._inner.query_decisions(
            agent_ids=agent_ids, kinds=kinds, subject_id=subject_id,
            reviews=reviews, since=since, until=until, limit=limit,
        )

    async def reviewed_decision_ids(self, decision_ids: Sequence[str]) -> set[str]:
        return await self._inner.reviewed_decision_ids(decision_ids)

    async def count_decisions_by_graph(
        self,
        databases: Sequence[str],
        *,
        agent_ids: Sequence[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, int]:
        return await self._inner.count_decisions_by_graph(
            databases, agent_ids=agent_ids, since=since, until=until
        )

    # --- Multi-graph management (pass-through) ---

    @property
    def backend_name(self) -> str:
        return self._inner.backend_name

    @property
    def current_database(self) -> str:
        return self._inner.current_database

    @property
    def graph_guard(self) -> GraphGuard:
        # The inner backend's own guard, not a second one: the thing being kept
        # still is its active graph, and two guards over one piece of state
        # would agree only by accident.
        return self._inner.graph_guard

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
