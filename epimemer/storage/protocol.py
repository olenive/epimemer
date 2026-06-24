"""Storage backend protocol.

Defines the interface that all storage backends must implement.
The protocol is designed to be storage-agnostic — SurrealDB, Postgres,
in-memory, or anything else can implement it.
"""

from datetime import datetime
from typing import Protocol, Sequence

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


class StorageBackend(Protocol):
    """Protocol for all storage backends."""

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        """Store a raw document. Returns the document id."""
        ...

    async def get_document(self, doc_id: str) -> RawDocument | None:
        """Retrieve a document by id."""
        ...

    # --- Segments ---

    async def store_segment(self, segment: Segment) -> str:
        """Store a segment. Returns the segment id."""
        ...

    async def get_segments_for_document(self, doc_id: str) -> Sequence[Segment]:
        """Retrieve all segments for a document."""
        ...

    # --- Epistemic Nodes ---

    async def store_node(self, node: EpistemicNode) -> str:
        """Store an epistemic node (Topic, Fact, or Inference). Returns the node id."""
        ...

    async def get_node(self, node_id: str) -> EpistemicNode | None:
        """Retrieve a node by id."""
        ...

    async def query_nodes(
        self,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
        at_time: datetime | None = None,
    ) -> Sequence[EpistemicNode]:
        """Query nodes by type, status, and/or temporal filter."""
        ...

    async def update_node_status(
        self,
        node_id: str,
        status: NodeStatus,
        superseded_at: datetime | None = None,
    ) -> None:
        """Update a node's status (e.g., mark as superseded or merged)."""
        ...

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        """Store an edge. Returns the edge id."""
        ...

    async def get_edges_from(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        """Get outgoing edges from a node, optionally filtered by type."""
        ...

    async def get_edges_to(
        self, node_id: str, *, edge_type: EdgeType | None = None
    ) -> Sequence[NodeEdge]:
        """Get incoming edges to a node, optionally filtered by type."""
        ...

    # --- Embeddings ---

    async def store_embedding(self, embedding: EmbeddingRecord) -> str:
        """Store an embedding record. Returns the embedding id."""
        ...

    async def get_embeddings_for_item(
        self, item_id: str, model_id: str | None = None
    ) -> Sequence[EmbeddingRecord]:
        """Get embeddings for an item, optionally filtered by model."""
        ...

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
    ) -> Sequence[tuple[str, float]]:
        """Find the k nearest items by vector similarity.

        Returns a sequence of (item_id, similarity_score) pairs,
        ordered by descending similarity.
        """
        ...

    # --- Timelines ---

    async def store_timeline(self, timeline: Timeline) -> str:
        """Store a timeline. Returns the timeline id."""
        ...

    async def get_timeline(self, timeline_id: str) -> Timeline | None:
        """Retrieve a timeline by id."""
        ...

    async def query_timelines(self) -> Sequence[Timeline]:
        """Retrieve all timelines."""
        ...

    # --- Metacontexts ---

    async def store_metacontext(self, mc: Metacontext) -> str:
        """Store a metacontext. Returns the metacontext id."""
        ...

    async def get_metacontext(self, mc_id: str) -> Metacontext | None:
        """Retrieve a metacontext by id."""
        ...

    async def query_metacontexts(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[Metacontext]:
        """Query metacontexts by status."""
        ...

    # --- Multi-graph management ---

    @property
    def current_database(self) -> str:
        """The name of the currently active database/graph."""
        ...

    async def list_databases(self) -> list[str]:
        """List available databases/graphs."""
        ...

    async def switch_database(self, database: str) -> None:
        """Switch to a different database/graph."""
        ...

    async def delete_database(self, database: str) -> None:
        """Delete a database/graph permanently."""
        ...

    # ----------------------------------------------------------------
    # VIZ / ADMIN READS — these methods MUST NEVER be registered as
    # MCP tools or imported in epimemer/mcp/. They return potentially
    # large result sets intended for the visualization dashboard only.
    # ----------------------------------------------------------------

    async def viz_list_nodes(
        self,
        database: str,
        *,
        historical_status: NodeStatus = NodeStatus.ACTIVE,
    ) -> Sequence[EpistemicNode]:
        """List all nodes in a graph, for visualization snapshot.

        Reads from the specified database without switching the active connection.
        """
        ...

    async def viz_list_edges(
        self,
        database: str,
    ) -> Sequence[NodeEdge]:
        """List all edges in a graph, for visualization snapshot.

        Reads from the specified database without switching the active connection.
        """
        ...
