"""In-memory storage backend.

Implements the StorageBackend protocol using plain dictionaries.
Supports multiple named graphs via a dict-of-dicts pattern.
Vector search uses brute-force cosine similarity.
"""

import copy
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence, TypeVar

from pydantic import BaseModel

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
    migration_excluded,
)
from epimemer.storage.protocol import normalize_for_storage, validate_graph_name

_M = TypeVar("_M", bound=BaseModel)


def _copy(model: _M) -> _M:
    """Deep-copy a record crossing the storage boundary.

    The store must not share object identity with its callers in either
    direction: a caller mutating a returned record would otherwise rewrite the
    store with no write call, and mutating an object after storing it would
    change what was stored. A serializing backend cannot behave that way, so
    aliasing here would let code pass in-memory that is broken in production.
    """
    return model.model_copy(deep=True)


def _store(model: _M) -> _M:
    """Prepare a record for the store: normalized, then detached from the caller.

    Writes go through here rather than `_copy` so this backend applies the same
    None-dropping every backend does. Without it the in-memory store would be
    *more* faithful than a serializing one, and a caller that wrote
    `metadata={"note": None}` would read it back here and lose it in production
    — exactly the kind of divergence that lets broken code pass its tests.
    """
    return _copy(normalize_for_storage(model))


def _copy_all(models: Iterable[_M]) -> list[_M]:
    return [m.model_copy(deep=True) for m in models]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_NODE_TYPE_TO_CLASS: dict[NodeType, type] = {
    NodeType.TOPIC: Topic,
    NodeType.FACT: Fact,
    NodeType.INFERENCE: Inference,
}

_CLASS_TO_NODE_TYPE: dict[type, NodeType] = {v: k for k, v in _NODE_TYPE_TO_CLASS.items()}


@dataclass
class _GraphStore:
    """All per-graph data bundled together."""
    documents: dict[str, RawDocument] = field(default_factory=dict)
    segments: dict[str, Segment] = field(default_factory=dict)
    nodes: dict[str, EpistemicNode] = field(default_factory=dict)
    edges: dict[str, NodeEdge] = field(default_factory=dict)
    # Endpoint indexes over `edges`: node id → the ids of edges at that endpoint.
    # Derived state — `_put_edge` and `_drop_edge` are their only writers, so
    # that no write path can update `edges` and forget these.
    by_src: dict[str, set[str]] = field(default_factory=dict)
    by_dst: dict[str, set[str]] = field(default_factory=dict)
    embeddings: dict[str, EmbeddingRecord] = field(default_factory=dict)
    timelines: dict[str, Timeline] = field(default_factory=dict)
    metacontexts: dict[str, Metacontext] = field(default_factory=dict)
    stores_since_reflect: int = 0
    reflect_threshold_override: int | None = None


def _index_edge(g: _GraphStore, edge: NodeEdge) -> None:
    g.by_src.setdefault(edge.src_id, set()).add(edge.id)
    g.by_dst.setdefault(edge.dst_id, set()).add(edge.id)


def _unindex_edge(g: _GraphStore, edge: NodeEdge) -> None:
    """Remove an edge's index entries, using the endpoints it currently has.

    Call this *before* re-pointing an edge in place; afterwards its old
    endpoints are unrecoverable and the entries under them become orphans.
    """
    for index, key in ((g.by_src, edge.src_id), (g.by_dst, edge.dst_id)):
        ids = index.get(key)
        if ids is None:
            continue
        ids.discard(edge.id)
        if not ids:
            # Drop the key rather than leave an empty set: on a long-lived graph
            # the index would otherwise accumulate an entry per node ever seen.
            del index[key]


def _put_edge(g: _GraphStore, edge: NodeEdge) -> str:
    """Insert or replace an edge and keep the endpoint indexes exact.

    Writes are upserts by id, so an edge stored again under different endpoints
    has to be un-indexed from the old ones first.
    """
    previous = g.edges.get(edge.id)
    if previous is not None:
        _unindex_edge(g, previous)
    g.edges[edge.id] = edge
    _index_edge(g, edge)
    return edge.id


def _drop_edge(g: _GraphStore, edge_id: str) -> None:
    edge = g.edges.pop(edge_id, None)
    if edge is not None:
        _unindex_edge(g, edge)


def _edges_at(
    g: _GraphStore,
    index: dict[str, set[str]],
    node_id: str,
    edge_type: EdgeType | None,
) -> list[NodeEdge]:
    """Edges at one endpoint, from the index rather than a scan of every edge.

    The type filter is applied after the index lookup: edges per node are few,
    so narrowing them costs nothing, while indexing by (node, type) would double
    the bookkeeping for no gain.
    """
    return _copy_all(
        edge
        for edge in (g.edges[edge_id] for edge_id in index.get(node_id, ()))
        if edge_type is None or edge.type == edge_type
    )


_DEFAULT_DB = "default"


class InMemoryStorage:
    """In-memory implementation of StorageBackend.

    Supports multiple named graphs. Each graph is an isolated _GraphStore.
    """

    def __init__(self):
        self._database: str = _DEFAULT_DB
        self._graphs: dict[str, _GraphStore] = {_DEFAULT_DB: _GraphStore()}

    # --- Lifecycle ---
    # No external connection to manage; these exist so the full StorageBackend
    # protocol is implemented and callers need no hasattr guards.

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @property
    def _g(self) -> _GraphStore:
        """The active graph's data store."""
        return self._graphs[self._database]

    # Convenience accessors for the active graph's collections.
    # Useful for tests and introspection.

    @property
    def documents(self) -> dict[str, RawDocument]:
        return self._g.documents

    @property
    def segments(self) -> dict[str, Segment]:
        return self._g.segments

    @property
    def nodes(self) -> dict[str, EpistemicNode]:
        return self._g.nodes

    @property
    def edges(self) -> dict[str, NodeEdge]:
        return self._g.edges

    @property
    def embeddings(self) -> dict[str, EmbeddingRecord]:
        return self._g.embeddings

    @property
    def timelines(self) -> dict[str, Timeline]:
        return self._g.timelines

    @property
    def metacontexts(self) -> dict[str, Metacontext]:
        return self._g.metacontexts

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        self._g.documents[doc.id] = _store(doc)
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        doc = self._g.documents.get(doc_id)
        return None if doc is None else _copy(doc)

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        for doc in self._g.documents.values():
            if doc.source == source:
                return _copy(doc)
        return None

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        self._g.segments[segment.id] = _store(segment)
        return segment.id

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        return _copy_all(
            s for s in self._g.segments.values() if s.source_id == doc_id
        )

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        self._g.nodes[node.id] = _store(node)
        return node.id

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        node = self._g.nodes.get(node_id)
        return None if node is None else _copy(node)

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
        at_time: datetime | None = None,
    ) -> Sequence[EpistemicNode]:
        results = []
        for node in self._g.nodes.values():
            # Filter by type
            if node_type is not None:
                expected_class = _NODE_TYPE_TO_CLASS[node_type]
                if not isinstance(node, expected_class):
                    continue

            # Filter by status (when not doing temporal query)
            if at_time is None:
                if node.status != status:
                    continue
            else:
                # Temporal query: include nodes that were active at the given time
                if node.created_at > at_time:
                    continue
                if node.superseded_at is not None and node.superseded_at <= at_time:
                    continue

            results.append(node)
        return _copy_all(results)

    async def get_node_by_content(
        self,
        content: str,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> EpistemicNode | None:
        """First active node with exactly this content (for exact-name upsert)."""
        for node in self._g.nodes.values():
            if node.status != status or node.content != content:
                continue
            if node_type is not None and not isinstance(
                node, _NODE_TYPE_TO_CLASS[node_type]
            ):
                continue
            return _copy(node)
        return None

    async def query_changes(
        self,
        *,
        start: datetime,
        end: datetime,
        node_type: NodeType | None = None,
    ) -> Sequence[EpistemicNode]:
        results = []
        for node in self._g.nodes.values():
            if node_type is not None:
                expected_class = _NODE_TYPE_TO_CLASS[node_type]
                if not isinstance(node, expected_class):
                    continue

            born = start <= node.created_at < end
            retired = (
                node.superseded_at is not None
                and start <= node.superseded_at < end
            )
            if born or retired:
                results.append(node)
        return _copy_all(results)

    async def update_node_status(
        self,
        node_id: str,
        status: NodeStatus,
        superseded_at: datetime | None = None,
    ) -> None:
        node = self._g.nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id} not found")
        node.status = status
        node.superseded_at = superseded_at

    async def relabel_edges(self, old_label: str, new_label: str) -> int:
        """Rewrite the label on all user-tier edges from old_label to new_label.

        Edge-label consolidation: edges are not versioned, so this is an in-place
        update. Returns the number of edges relabelled.
        """
        n = 0
        for edge in self._g.edges.values():
            if edge.type == EdgeType.RELATED and edge.label == old_label:
                edge.label = new_label
                n += 1
        return n

    async def get_relation_kind(self, label: str) -> str | None:
        for edge in self._g.edges.values():
            if edge.type == EdgeType.RELATED and edge.label == label:
                return edge.kind
        return None

    async def count_nodes_by_type(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> dict[NodeType, int]:
        counts = {nt: 0 for nt in NodeType}
        for node in self._g.nodes.values():
            if node.status != status:
                continue
            counts[_CLASS_TO_NODE_TYPE[type(node)]] += 1
        return counts

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        return _put_edge(self._g, _store(edge))

    async def delete_edge(self, edge_id: str) -> None:
        _drop_edge(self._g, edge_id)

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return _edges_at(self._g, self._g.by_src, node_id, edge_type)

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return _edges_at(self._g, self._g.by_dst, node_id, edge_type)

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        counts = {et: 0 for et in EdgeType}
        for edge in self._g.edges.values():
            counts[edge.type] += 1
        return counts

    # --- Atomic compound operations ---

    def _migrate_edges_inplace(self, old_ids: set[str], new_id: str) -> None:
        """Re-point non-history edges touching old_ids onto new_id, in place.

        Drops self-loops (where two merged sources were connected) and collapses
        duplicate (src, dst, type) edges, keeping one per group. The new node is
        assumed to start with no edges, so dedup only tracks edges migrated here.
        """
        seen_signatures: set[tuple[str, str, str]] = set()
        for edge in list(self._g.edges.values()):
            if migration_excluded(edge):
                continue
            if edge.src_id not in old_ids and edge.dst_id not in old_ids:
                continue
            new_src = new_id if edge.src_id in old_ids else edge.src_id
            new_dst = new_id if edge.dst_id in old_ids else edge.dst_id
            if new_src == edge.src_id and new_dst == edge.dst_id:
                continue
            signature = (new_src, new_dst, edge.type.value)
            if new_src == new_dst or signature in seen_signatures:
                _drop_edge(self._g, edge.id)
                continue
            seen_signatures.add(signature)
            # Un-index against the old endpoints while they are still readable.
            _unindex_edge(self._g, edge)
            edge.src_id = new_src
            edge.dst_id = new_dst
            _index_edge(self._g, edge)

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
        # Snapshot the active graph so any failure leaves it untouched.
        snapshot = copy.deepcopy(self._g)
        try:
            node = self._g.nodes.get(old_node.id)
            if node is None:
                raise KeyError(f"Node {old_node.id} not found")
            node.status = NodeStatus.SUPERSEDED
            node.superseded_at = superseded_at
            self._g.nodes[new_node.id] = _store(new_node)
            self._g.embeddings[new_embedding.id] = _store(new_embedding)
            self._migrate_edges_inplace({old_node.id}, new_node.id)
            _put_edge(self._g, _store(lineage_edge))
            for edge in evidence_edges:
                _put_edge(self._g, _store(edge))
            for edge_id in clear_edge_ids:
                _drop_edge(self._g, edge_id)
        except Exception:
            self._graphs[self._database] = snapshot
            raise

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
        snapshot = copy.deepcopy(self._g)
        try:
            node = self._g.nodes.get(old_node.id)
            if node is None:
                raise KeyError(f"Node {old_node.id} not found")
            node.status = NodeStatus.SUPERSEDED
            node.superseded_at = superseded_at
            # No new node, no embedding, no migration — the existing node stands.
            _put_edge(self._g, _store(lineage_edge))
            for edge in evidence_edges:
                _put_edge(self._g, _store(edge))
            for edge_id in clear_edge_ids:
                _drop_edge(self._g, edge_id)
        except Exception:
            self._graphs[self._database] = snapshot
            raise

    async def merge_nodes_tx(
        self,
        source_nodes: Sequence[EpistemicNode],
        merged_node: EpistemicNode,
        merged_embedding: EmbeddingRecord,
        lineage_edges: Sequence[NodeEdge],
        *,
        merged_at: datetime,
    ) -> None:
        snapshot = copy.deepcopy(self._g)
        try:
            self._g.nodes[merged_node.id] = _store(merged_node)
            self._g.embeddings[merged_embedding.id] = _store(merged_embedding)
            # Migrate before writing lineage edges so they are not re-pointed.
            self._migrate_edges_inplace({s.id for s in source_nodes}, merged_node.id)
            for source in source_nodes:
                node = self._g.nodes.get(source.id)
                if node is None:
                    raise KeyError(f"Node {source.id} not found")
                node.status = NodeStatus.MERGED
                node.superseded_at = merged_at
            for edge in lineage_edges:
                _put_edge(self._g, _store(edge))
        except Exception:
            self._graphs[self._database] = snapshot
            raise

    async def write_batch_tx(
        self,
        *,
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
    ) -> None:
        # Pure inserts with new ids → track and undo on failure (cheaper than a
        # full-graph snapshot).
        added_nodes: list[str] = []
        added_edges: list[str] = []
        added_embeddings: list[str] = []
        try:
            for node in nodes:
                self._g.nodes[node.id] = _store(node)
                added_nodes.append(node.id)
            for edge in edges:
                _put_edge(self._g, _store(edge))
                added_edges.append(edge.id)
            for embedding in embeddings:
                self._g.embeddings[embedding.id] = _store(embedding)
                added_embeddings.append(embedding.id)
        except Exception:
            for node_id in added_nodes:
                self._g.nodes.pop(node_id, None)
            for edge_id in added_edges:
                _drop_edge(self._g, edge_id)
            for embedding_id in added_embeddings:
                self._g.embeddings.pop(embedding_id, None)
            raise

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        self._g.embeddings[embedding.id] = _store(embedding)
        return embedding.id

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        results = []
        for emb in self._g.embeddings.values():
            if emb.item_id != item_id:
                continue
            if model_id is not None and emb.model_id != model_id:
                continue
            results.append(emb)
        return _copy_all(results)

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
    ) -> Sequence[tuple[str, float]]:
        candidates: list[tuple[str, float]] = []
        for emb in self._g.embeddings.values():
            if emb.model_id != model_id:
                continue
            node = self._g.nodes.get(emb.item_id)
            # Only active epistemic nodes are retrievable. Superseded/merged
            # nodes must never resurface via vector search, mirroring the
            # status guard in query_nodes.
            if node is None or node.status != NodeStatus.ACTIVE:
                continue
            if node_type is not None:
                expected_class = _NODE_TYPE_TO_CLASS[node_type]
                if not isinstance(node, expected_class):
                    continue
            similarity = _cosine_similarity(query_vector, emb.vector)
            candidates.append((emb.item_id, similarity))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:k]

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        self._g.timelines[timeline.id] = _store(timeline)
        return timeline.id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        timeline = self._g.timelines.get(timeline_id)
        return None if timeline is None else _copy(timeline)

    async def query_timelines(self) -> Sequence[Timeline]:
        return _copy_all(self._g.timelines.values())

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        self._g.metacontexts[mc.id] = _store(mc)
        return mc.id

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        mc = self._g.metacontexts.get(mc_id)
        return None if mc is None else _copy(mc)

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        return _copy_all(
            mc for mc in self._g.metacontexts.values() if mc.status == status
        )

    # --- Reflection bookkeeping ---

    async def get_reflect_counter(self) -> int:
        return self._g.stores_since_reflect

    async def bump_reflect_counter(self) -> int:
        self._g.stores_since_reflect += 1
        return self._g.stores_since_reflect

    async def reset_reflect_counter(self) -> int:
        previous = self._g.stores_since_reflect
        self._g.stores_since_reflect = 0
        return previous

    async def get_reflect_threshold_override(self) -> int | None:
        return self._g.reflect_threshold_override

    async def set_reflect_threshold_override(self, threshold: int | None) -> None:
        self._g.reflect_threshold_override = threshold

    # --- Multi-graph management ---

    @property
    def backend_name(self) -> str:
        return "memory"

    @property
    def current_database(self) -> str:
        return self._database

    async def list_databases(self) -> list[str]:
        return sorted(self._graphs.keys())

    async def switch_database(self, database: str) -> None:
        validate_graph_name(database)
        if database not in self._graphs:
            self._graphs[database] = _GraphStore()
        self._database = database

    async def delete_database(self, database: str) -> None:
        validate_graph_name(database)
        if database == self._database:
            raise ValueError(f"Cannot delete the currently active graph '{database}'")
        if database not in self._graphs:
            raise KeyError(f"Graph '{database}' does not exist")
        del self._graphs[database]

    # --- Viz reads (cross-graph, no switching) ---

    async def viz_list_nodes(
        self,
        database: str,
        *,
        historical_status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[EpistemicNode]:
        graph = self._graphs.get(database)
        if graph is None:
            return []
        return [n for n in graph.nodes.values() if n.status == historical_status]

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        graph = self._graphs.get(database)
        if graph is None:
            return []
        return list(graph.edges.values())

    async def viz_list_timelines(
        self,
        database: str,
    ) -> Sequence[Timeline]:
        graph = self._graphs.get(database)
        if graph is None:
            return []
        return _copy_all(graph.timelines.values())

    async def viz_list_metacontexts(
        self,
        database: str,
    ) -> Sequence[Metacontext]:
        graph = self._graphs.get(database)
        if graph is None:
            return []
        return _copy_all(
            mc for mc in graph.metacontexts.values() if mc.status == NodeStatus.ACTIVE
        )
