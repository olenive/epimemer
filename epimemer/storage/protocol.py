"""Storage backend protocol.

Defines the interface that all storage backends must implement.
The protocol is designed to be storage-agnostic — SurrealDB, Postgres,
in-memory, or anything else can implement it.
"""

import re
from datetime import datetime
from typing import Protocol, Sequence, TypeVar

from pydantic import BaseModel

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

_M = TypeVar("_M", bound=BaseModel)

GRAPH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_graph_name(name: str) -> str:
    """Reject graph names that are not a plain identifier. Returns the name.

    Graph names arrive from agent-facing MCP tools as arbitrary strings and
    reach SurrealQL that cannot fully parameterize a database name, so a name
    containing a backtick can close the quoting and append its own statements.
    Every backend applies this so the two agree on what a legal graph name is —
    a name one backend accepts must never be one the other would interpolate.
    """
    if not isinstance(name, str) or not GRAPH_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid graph name {name!r}. Graph names must be 1-64 characters "
            "of letters, digits, hyphen or underscore."
        )
    return name


def resolve_reflect_threshold(override: int | None, default: int) -> int:
    """The one rule for which reflect threshold wins: a graph's override, else
    the process default.

    Trivial, and deliberately shared rather than inlined. It lives beside the
    protocol methods that store the override because both the MCP tools and the
    visualization instrumentation resolve it, and a second copy is how a badge
    starts showing a number the ingest path does not judge against.
    """
    return default if override is None else override


def drop_none_values(value):
    """Remove None-valued keys from dicts, recursively. Returns a new value.

    Python has one "nothing" (``None``); SurrealDB has two — ``NULL`` (the key
    exists, its value is nothing) and ``NONE`` (the key does not exist). They do
    not compare equal, and the Python driver has no way to express ``NULL`` for
    a parameterized value: every ``None`` is encoded as ``NONE``, so the key is
    simply absent from the stored row.

    Rather than let that produce different behaviour per backend, every backend
    normalizes the same way *before* writing: a None-valued key is dropped, so
    absence is the single representation of "no information". In a free-form
    bag like `metadata`, `{"note": None}` and `{}` mean the same thing, so
    nothing recoverable is lost.

    Note the asymmetry, which mirrors SurrealDB exactly: a None *inside a list*
    is preserved, because arrays keep their positions and dropping an element
    would shift every index after it.

    Declared model fields are unaffected in practice — they are absent from the
    stored row either way, and Pydantic refills their `= None` default on read.
    """
    if isinstance(value, dict):
        return {k: drop_none_values(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_none_values(v) for v in value]
    return value


def normalize_for_storage(model: _M) -> _M:
    """Return a copy of `model` with None-valued keys dropped from dict fields.

    Every backend applies this on the way in, so a record round-trips
    identically whichever backend is configured. See `drop_none_values`.
    """
    updates = {
        name: drop_none_values(attr)
        for name in type(model).model_fields
        if isinstance(attr := getattr(model, name, None), dict)
    }
    return model.model_copy(update=updates) if updates else model


class StorageBackend(Protocol):
    """Protocol for all storage backends.

    **`store_*` is upsert by id.** Storing a record whose id already exists
    replaces it in place; it must not duplicate the record and must not
    silently do nothing. Every caller relies on this — `apply_decay` re-stores
    decayed nodes, `add_timeline_timepoint` re-stores the timeline — so a
    backend that treats `store_*` as insert-only loses those writes without
    raising. `write_batch_tx` is the deliberate exception; see its docstring.

    **Records round-trip unchanged.** What `store_*` accepts is what the
    matching getter returns: content is preserved byte-for-byte (unicode,
    quotes, backticks, `$`, newlines — a backend that reaches its store through
    query text must parameterize rather than interpolate), floats keep their
    precision, and nested structure is preserved.

    **The one documented exception is None-valued dict keys**, which every
    backend drops on the way in by calling `normalize_for_storage`. Callers must
    treat absence as the only representation of "no information" in free-form
    dicts like `metadata`; `{"note": None}` is stored, and reads back, as `{}`.
    A new backend that skips this normalization will *appear* to work — it will
    simply be more faithful than the others — and will diverge the moment a
    caller writes a None. Apply it.

    Backend parity is covered by `tests/storage/test_storage_parity.py`, which
    runs the same assertions against every implementation. A new backend should
    be added to the fixture there and in `tests/conftest.py`; if it passes both,
    it satisfies this contract.
    """

    # --- Lifecycle ---

    async def connect(self) -> None:
        """Open any underlying connection. Called once at startup.

        A backend with nothing to open (e.g. in-memory) implements this as a
        no-op so callers can invoke it unconditionally.
        """
        ...

    async def close(self) -> None:
        """Release any underlying connection. Called once at shutdown.

        A no-op for backends holding no external resources.
        """
        ...

    # --- Documents ---

    async def store_document(self, doc: RawDocument) -> str:
        """Store a raw document. Returns the document id."""
        ...

    async def get_document(self, doc_id: str) -> RawDocument | None:
        """Retrieve a document by id."""
        ...

    async def get_document_by_source(self, source: str) -> RawDocument | None:
        """First document whose `source` name matches — lets find_nodes resolve a
        source by its human name (e.g. "ISSUES.md"), not just its id."""
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

    async def get_node_by_content(
        self,
        content: str,
        *,
        node_type: NodeType | None = None,
        status: NodeStatus = NodeStatus.ACTIVE,
    ) -> EpistemicNode | None:
        """First node with exactly this content — for exact-name upsert of
        source/tag entity nodes (so a repeated name reuses one node)."""
        ...

    async def query_changes(
        self,
        *,
        start: datetime,
        end: datetime,
        node_type: NodeType | None = None,
    ) -> Sequence[EpistemicNode]:
        """Nodes whose creation or retirement falls in the half-open window
        [start, end).

        A node matches if its `created_at` is in the window (born) or its
        `superseded_at` is in the window (retired — covers both supersession and
        merge). Returns plain nodes regardless of status; callers derive the
        specific lifecycle events from each node's timestamps and status.
        """
        ...

    async def update_node_status(
        self,
        node_id: str,
        status: NodeStatus,
        superseded_at: datetime | None = None,
    ) -> None:
        """Update a node's status (e.g., mark as superseded or merged)."""
        ...

    async def relabel_edges(self, old_label: str, new_label: str) -> int:
        """Rewrite the label on user-tier (RELATED) edges in place. Returns the
        count relabelled. Edges are not versioned, so this is a plain update.
        Used by edge-label consolidation."""
        ...

    async def get_relation_kind(self, label: str) -> str | None:
        """The kind of any existing user-tier edge with this label, or None.
        Lets a coined label reuse its kind (classified once per label)."""
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

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        retired_at: datetime | None,
    ) -> None:
        """Atomically move every node in ``nodes`` to ``status``.

        No node, edge or embedding is created or destroyed — only the status
        and ``superseded_at`` (the instant the node left the active set, which
        is what temporal queries read; pass None when returning to ACTIVE).

        Archival is the reason this exists: the caller exports the nodes first,
        and the flip must not apply partially, because a half-flipped batch
        makes the report of what was archived untrue. Restoring runs the same
        operation in the other direction.

        Raises if any node is missing, before flipping any of them.
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

        Unlike the `store_*` methods this is **insert-only, not upsert**: ids
        are assumed new (these are freshly-created records). Re-writing an
        existing id through this path is a caller error, not an update.
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

    # --- Reflection bookkeeping ---

    async def get_reflect_counter(self) -> int:
        """Stores recorded in the active graph since its last reflect.

        The counter belongs to the graph, not the process: it lives beside the
        data it describes, so a persistent graph carries it across server
        restarts and client reconnects, and switching graphs switches counters.
        Absent state reads as 0.
        """
        ...

    async def bump_reflect_counter(self) -> int:
        """Record one store against the active graph. Returns the new count."""
        ...

    async def reset_reflect_counter(self) -> int:
        """Zero the active graph's counter. Returns the count before the reset."""
        ...

    async def get_reflect_threshold_override(self) -> int | None:
        """The active graph's threshold override, or None if it has none.

        Stored beside the counter and scoped the same way, so a graph carries
        its own answer to "how many stores before you suggest reflecting?"
        across restarts. None means the graph follows the process default —
        deliberately not the default's current *value*, so that changing the
        configured default later still reaches graphs that were once overridden
        and then cleared.
        """
        ...

    async def set_reflect_threshold_override(self, threshold: int | None) -> None:
        """Set the active graph's threshold override, or clear it with None."""
        ...

    # --- Multi-graph management ---

    @property
    def backend_name(self) -> str:
        """Short, human-readable backend kind (e.g. "memory", "surrealdb").

        Surfaced to the visualization UI so a viewer can tell instantly whether
        they are looking at an ephemeral in-memory store or a persistent one.
        Every backend implements it explicitly — no duck-typing on the class
        name — so the label stays stable if a class is renamed.
        """
        ...

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

    async def viz_list_timelines(
        self,
        database: str,
    ) -> Sequence[Timeline]:
        """List all timelines in a graph, with their timepoints embedded.

        Distinct from `query_timelines`, which reads the *active* graph: the hub
        asks a single session for any of the graphs it can see, so this must
        name its target and leave the active connection where it found it.
        """
        ...

    async def viz_list_metacontexts(
        self,
        database: str,
    ) -> Sequence[Metacontext]:
        """List all active metacontexts in a graph, for visualization.

        The dashboard filters by epistemic frame, and `has_metacontext` edges
        carry only ids — without the metacontexts themselves a viewer could
        offer no better than a list of UUIDs.
        """
        ...
