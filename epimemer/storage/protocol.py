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

    async def count_nodes_by_type(
        self,
        *,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> dict[NodeType, int]:
        """Count nodes grouped by type, filtered by status.

        Backends should implement this as an aggregate query rather than
        materializing nodes, so it stays cheap on large graphs.
        """
        ...

    # --- Edges ---

    async def store_edge(self, edge: NodeEdge) -> str:
        """Store an edge. Returns the edge id."""
        ...

    async def delete_edge(self, edge_id: str) -> None:
        """Delete an edge by id. A no-op if the edge does not exist."""
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

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        """Count edges grouped by edge type.

        Backends should implement this as an aggregate query rather than
        materializing edges, so it stays cheap on large graphs.
        """
        ...

    # --- Atomic compound operations ---
    #
    # Supersession and merge each perform several writes (status change, node +
    # embedding store, edge migration, lineage edge). They are exposed as single
    # protocol methods so each backend can make them atomic — all-or-nothing —
    # rather than leaving a half-applied graph if a step fails midway.

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
        """Atomically supersede ``old_node`` with ``new_node``.

        Marks the old node superseded, stores and embeds the new node, migrates
        the old node's non-history edges onto the new node, and persists
        ``lineage_edge`` (the superseded_by edge). Also inserts ``evidence_edges``
        (e.g. evidence_superseded flags on dependent inferences) and deletes
        ``clear_edge_ids`` (e.g. resolved supersession_candidate edges). If any
        step fails, the whole operation is rolled back.
        """
        ...

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
        """Atomically supersede ``old_node`` by an already-existing node.

        Marks the old node superseded and persists ``lineage_edge`` (superseded_by,
        old → existing). Unlike ``supersede_node_tx`` it does **not** create a new
        node, embed, or migrate edges — the existing node carries its own
        evidence. Inserts ``evidence_edges`` and deletes ``clear_edge_ids`` as
        above. All-or-nothing.
        """
        ...

    async def merge_nodes_tx(
        self,
        source_nodes: Sequence[EpistemicNode],
        merged_node: EpistemicNode,
        merged_embedding: EmbeddingRecord,
        lineage_edges: Sequence[NodeEdge],
        *,
        merged_at: datetime,
    ) -> None:
        """Atomically merge ``source_nodes`` into ``merged_node``.

        Stores and embeds the merged node, migrates the sources' non-history
        edges onto it (dropping self-loops where two merged sources were
        connected, and collapsing duplicate edges to one per src/dst/type),
        marks each source merged, and persists ``lineage_edges`` (the
        merged_into edges). If any step fails, the whole operation is rolled
        back.
        """
        ...

    async def write_batch_tx(
        self,
        *,
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
    ) -> None:
        """Atomically insert a batch of nodes, edges, and embeddings.

        All-or-nothing pure insert (no status changes or deletes). Used by the
        multi-write paths that compute their full output in Python before
        persisting — ingest (``store_decomposition``) and parent/subtopic
        synthesis — so a mid-operation failure cannot leave a partial graph.
        Ids are assumed new (these are freshly-created records).
        """
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
