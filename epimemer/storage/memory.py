"""In-memory storage backend for testing.

Implements the StorageBackend protocol using plain dictionaries.
Vector search uses brute-force cosine similarity.
"""

import math
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


class InMemoryStorage:
    """In-memory implementation of StorageBackend for testing."""

    def __init__(self):
        self.documents: dict[str, RawDocument] = {}
        self.segments: dict[str, Segment] = {}
        self.nodes: dict[str, EpistemicNode] = {}
        self.edges: dict[str, NodeEdge] = {}
        self.embeddings: dict[str, EmbeddingRecord] = {}
        self.timelines: dict[str, Timeline] = {}
        self.metacontexts: dict[str, Metacontext] = {}

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        self.documents[doc.id] = doc
        return doc.id

    async def get_document(self, doc_id: str) -> RawDocument | None:
        return self.documents.get(doc_id)

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        self.segments[segment.id] = segment
        return segment.id

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        return [s for s in self.segments.values() if s.source_id == doc_id]

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        self.nodes[node.id] = node
        return node.id

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        return self.nodes.get(node_id)

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
        at_time: datetime | None = None,
    ) -> Sequence[EpistemicNode]:
        results = []
        for node in self.nodes.values():
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
        return results

    async def update_node_status(
        self,
        node_id: str,
        status: NodeStatus,
        superseded_at: datetime | None = None,
    ) -> None:
        node = self.nodes.get(node_id)
        if node is None:
            raise KeyError(f"Node {node_id} not found")
        node.status = status
        node.superseded_at = superseded_at

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        self.edges[edge.id] = edge
        return edge.id

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return [
            e for e in self.edges.values()
            if e.src_id == node_id and (edge_type is None or e.type == edge_type)
        ]

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        return [
            e for e in self.edges.values()
            if e.dst_id == node_id and (edge_type is None or e.type == edge_type)
        ]

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        self.embeddings[embedding.id] = embedding
        return embedding.id

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        results = []
        for emb in self.embeddings.values():
            if emb.item_id != item_id:
                continue
            if model_id is not None and emb.model_id != model_id:
                continue
            results.append(emb)
        return results

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
    ) -> Sequence[tuple[str, float]]:
        # Collect all embeddings for the given model
        candidates: list[tuple[str, float]] = []
        for emb in self.embeddings.values():
            if emb.model_id != model_id:
                continue
            # Optionally filter by node type
            if node_type is not None:
                node = self.nodes.get(emb.item_id)
                if node is None:
                    continue
                expected_class = _NODE_TYPE_TO_CLASS[node_type]
                if not isinstance(node, expected_class):
                    continue
            similarity = _cosine_similarity(query_vector, emb.vector)
            candidates.append((emb.item_id, similarity))

        # Sort by similarity descending, return top k
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:k]

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        self.timelines[timeline.id] = timeline
        return timeline.id

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        return self.timelines.get(timeline_id)

    async def query_timelines(self) -> Sequence[Timeline]:
        return list(self.timelines.values())

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        self.metacontexts[mc.id] = mc
        return mc.id

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        return self.metacontexts.get(mc_id)

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        return [mc for mc in self.metacontexts.values() if mc.status == status]
