"""Storage backend protocol.

Defines the interface that all storage backends must implement.
The protocol is designed to be storage-agnostic — SurrealDB, Postgres,
in-memory, or anything else can implement it.
"""

import re
from datetime import datetime
from typing import Literal, Protocol, Sequence, TypeVar

from pydantic import BaseModel

from epimemer.core.types import (
    DEFAULT_MERGE_CYCLE_LIMIT,
    Agent,
    DecisionKind,
    DecisionRecord,
    JudgeRef,
    DEFAULT_MERGE_UNDO_DEPTH,
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
from epimemer.storage.active_graph import GraphGuard

_M = TypeVar("_M", bound=BaseModel)

GRAPH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Which endpoint of an edge a lookup matches on — the src (`"from"`, outgoing)
# or the dst (`"to"`, incoming). Named rather than boolean because a caller
# reading `direction="to"` needs no comment and `incoming=True` does.
EdgeDirection = Literal["from", "to"]


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


def resolve_require_judge(override: bool | None, default: bool) -> bool:
    """Whether this graph refuses a write that names no judge.

    A graph's own answer wins; `None` means *follow the process default*, and
    deliberately not the default's current **value** — so changing the
    configured default later still reaches a graph that was once set and then
    cleared. The same rule `resolve_reflect_threshold` states, for the same
    reason.
    """
    return default if override is None else override


class MergeOverrides(BaseModel):
    """A graph's own answers about merging, where it has any.

    `None` on a field means *follow the process default* — deliberately not the
    default's current value, so that changing a default later still reaches a
    graph that was once overridden and then cleared. Same rule as the reflect
    threshold, and stated again rather than shared because the two are stored
    apart and a reader of one should not have to find the other.
    """

    undo_depth: int | None = None
    cycle_limit: int | None = None


class MergeSettings(BaseModel):
    """The numbers actually in force for a merge on this graph."""

    undo_depth: int = DEFAULT_MERGE_UNDO_DEPTH
    cycle_limit: int = DEFAULT_MERGE_CYCLE_LIMIT


def resolve_merge_settings(overrides: MergeOverrides | None) -> MergeSettings:
    """The merge numbers in force: the graph's overrides, else the defaults.

    Shared for the reason `resolve_reflect_threshold` is shared — the tools, the
    merge path and anything reporting the settings all resolve them, and a
    second copy is how a reported number drifts from the one the gate applies.
    """
    if overrides is None:
        return MergeSettings()
    return MergeSettings(**{
        field: value
        for field, value in overrides.model_dump().items()
        if value is not None
    })


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
    silently do nothing. Every caller relies on this — `_record_retrieval`
    re-stores each node a search returned, `add_timeline_timepoint` re-stores
    the timeline — so a backend that treats `store_*` as insert-only loses those
    writes without raising. `write_batch_tx` is the deliberate exception; see
    its docstring.

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

    async def get_segments(self, segment_ids: Sequence[str]) -> dict[str, Segment]:
        """`get_segments_for_document` by id instead of by document, batched.

        A lexical search over the segment corpus comes back with ids and scores;
        rendering those hits needs their text and their document. Nothing else
        in the protocol could fetch a segment by its own id — the only route was
        through the document that contains it, which a hit does not know.

        **Ids that are not segments are absent from the map**, rather than
        present with a `None` value, so `result.get(sid)` behaves exactly like a
        single-segment lookup would. This follows `get_nodes` rather than
        `get_edges_for`: a list has an empty value and a segment does not.
        Repeated ids collapse; an empty request returns `{}` without touching
        the store.
        """
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

    async def get_nodes(self, node_ids: Sequence[str]) -> dict[str, EpistemicNode]:
        """`get_node` for many ids in a bounded number of statements.

        A batching of *cost*, not of *answer*: each id maps to exactly what
        `get_node` would return for it, at any status.

        **Ids that are not nodes are absent from the map**, rather than present
        with a `None` value — so `result.get(node_id)` behaves exactly like
        `await get_node(node_id)` and callers keep the check they already write.
        This differs from `get_edges_for`, where every id gets a key: a list has
        an empty value and a node does not. Repeated ids collapse; an empty
        request returns `{}` without touching the store.

        Backends must not answer this with one statement per id — that is the
        cost it exists to remove. On SurrealDB a node's table is not known from
        its id, so the single-node form probes topic, then fact, then inference:
        1–3 round-trips each, and 2,104 of them in one `reflect` at 1,200 nodes
        (ISSUES.md #14).
        """
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

        A node matches if its `created_at` is in the window (born), its
        `superseded_at` is in the window (retired — covers both supersession and
        merge), or **any of its lifecycle episodes** began or ended there.

        The episodes are why the last clause exists: a node that retired,
        returned and retired again has one `superseded_at`, and a window over
        the first retirement would otherwise not match it at all. Returns plain
        nodes regardless of status; callers derive the specific lifecycle events
        from each node's episodes and timestamps.
        """
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

    async def get_edges_for(
        self,
        node_ids: Sequence[str],
        *,
        direction: EdgeDirection,
        edge_type: EdgeType | None = None,
    ) -> dict[str, list[NodeEdge]]:
        """`get_edges_from`/`get_edges_to` for many nodes in one round-trip.

        Returns `{node_id: edges}` with the same edges the single-node method
        would return for each id, so this is a batching of *cost*, not a change
        of *answer*. Reflection reads the edges of every active node several
        times over; asking once per node is what makes `reflect` round-trip
        bound on a networked backend (ISSUES.md #14).

        **Every requested id is a key**, mapping to `[]` when it has no matching
        edges — including ids that are not nodes at all. Callers iterate the map
        and a missing key would silently skip a node, so absence has to mean
        "you did not ask" rather than "there were none". Repeated ids collapse to
        one entry; an empty request returns `{}` without touching the store.

        A self-loop belongs to its node in both directions, exactly as the
        single-node methods report it.

        Backends must answer this in a bounded number of statements —
        chunking a large id list is fine, one query per id is the thing this
        exists to replace — and must return whole edges, since callers read
        `label`, `kind`, `weight` and `metadata` off them.
        """
        ...

    async def count_edges_by_type(self) -> dict[EdgeType, int]:
        """Count edges grouped by edge type.

        Backends should implement this as an aggregate query rather than
        materializing edges, so it stays cheap on large graphs.
        """
        ...

    # --- Atomic compound operations ---
    #
    # Every method here takes `judge: JudgeRef | None` and records it on the
    # lifecycle episode it writes — these are the calls that retire or return a
    # node, and *who decided that* belongs beside *when* (REVIEW_MODE.md §3).
    # The episode is built inside the backend, from the stored history (#67), so
    # the judge has to arrive as an argument rather than on the node objects.
    #
    # `None` means **unknown**, and nothing more (§3.3). Whether a graph accepts
    # an unknown judge is a per-graph policy, checked once at the MCP boundary;
    # a backend never refuses on this account, because a backend that held the
    # policy would be a second place for it to differ.
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
        status: NodeStatus,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        """Atomically supersede ``old_node`` with ``new_node``.

        ``status`` says *why* — `CORRECTED` or `HISTORICAL` (#53). It is passed
        in rather than assumed because the two are opposite events and only the
        caller knows which happened.

        Marks the old node with ``status`` and appends a lifecycle episode
        naming ``new_node`` as the counterpart (#57 — "superseded by whom" must
        be readable without joining on the lineage edge), stores and embeds the
        new node, migrates the old node's edges onto the new node per
        ``migration_disposition`` (history, review and judgment edges stay
        behind — a judgment was made against a wording, #65),
        and persists ``lineage_edge`` (the superseded_by edge). Also inserts
        ``evidence_edges``
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
        status: NodeStatus,
        superseded_at: datetime,
        evidence_edges: Sequence[NodeEdge] = (),
        clear_edge_ids: Sequence[str] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        """Atomically supersede ``old_node`` by an already-existing node.

        Marks the old node with ``status`` (see ``supersede_node_tx``), appends
        a lifecycle episode with ``existing_id`` as the counterpart, and
        persists ``lineage_edge`` (superseded_by, old → existing). Unlike
        ``supersede_node_tx`` it does **not** create a new node, embed, or
        migrate edges — the existing node carries its own evidence. Inserts
        ``evidence_edges`` and deletes ``clear_edge_ids`` as above.
        All-or-nothing.
        """
        ...

    async def set_node_status_tx(
        self,
        nodes: Sequence[EpistemicNode],
        *,
        status: NodeStatus,
        at: datetime,
        edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        """Atomically move every node in ``nodes`` to ``status``, as of ``at``.

        No node or embedding is created or destroyed, and ``edges`` is the one
        exception on the edge side: edges given here are written in the same
        transaction as the flip. Reactivating a claim needs it. A node put back
        to ACTIVE with no edge recording *why* is an assertion the graph makes
        and cannot attribute, and two transactions can leave exactly that state
        behind (#53 T2). Omitted — which is every other caller — this creates
        nothing, and archival's guarantee is unchanged.

        ``status`` says which direction this is and ``at`` is the instant it
        happened:

        - **A retirement** (any status but ACTIVE) sets ``superseded_at`` to
          ``at`` and appends a lifecycle episode with no counterpart, since
          nothing superseded the node — archival retires it for triviality.
        - **A return** (ACTIVE) clears ``superseded_at`` and closes the open
          episode at ``at``. A node with no open episode keeps an empty
          history rather than gaining an invented one.

        The instant is the caller's either way. A backend that reached for its
        own clock on the return would make the two halves of one round trip
        disagree about when it happened.

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
        evidence_edges: Sequence[NodeEdge] = (),
        judge: JudgeRef | None = None,
    ) -> None:
        """Atomically merge ``source_nodes`` into ``merged_node``.

        Stores and embeds the merged node, migrates the sources' edges onto it
        per ``migration_disposition`` — history, review and judgment edges stay
        on the source, the last because the survivor's content is *synthesised*
        and so is not the wording anything was judged against (#65) — dropping
        self-loops where two merged sources were connected, and collapsing
        duplicate edges to one per src/dst/type,
        marks each source merged — appending a lifecycle episode naming
        ``merged_node`` as the counterpart, so the history says where the
        content went rather than only that the node left — and persists
        ``lineage_edges`` (the merged_into edges). If any step fails, the whole
        operation is rolled back.

        ``evidence_edges`` are the review flags on dependent inferences
        (``evidence_merged``, #61), inserted inside the same transaction. They
        are anchored to the sources rather than the survivor, so they must be
        written *after* migration — a flag re-pointed onto the survivor would
        say a fact absorbed itself.
        """
        ...

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
        """Atomically undo one merge: put the sources back and destroy `survivor`.

        **This is the only hard delete in the system, and the only place a node
        is ever destroyed rather than retired.** Retirement is how things leave
        the active set and archival is an export; a merge survivor is the single
        exception, because its content was written by an agent, sourced from no
        document, and every claim it carried is restored to the sources it came
        from (REVIEW_MODE.md §7.7). **Never expose a general node delete through
        an MCP tool.** A system whose central rule is *nothing is destroyed*
        must not quietly acquire *delete anything* — and the next person wanting
        a hard delete will find whatever is built here and use it.

        Deleting the survivor also deletes its `EmbeddingRecord`s: the vector is
        stored per item, so removing the node alone strands an entry the index
        still returns. The cascade belongs here rather than to the caller.

        `restored_edges` are replayed exactly as they stood before the merge,
        including any that were between two merging sources and so were dropped
        outright as self-loops. `delete_edge_ids` names the edges the merge left
        behind — those it moved onto the survivor, its `merged_into` lineage,
        and the `evidence_merged` flags it wrote. Both are planned by
        `reverse_merge`, which also runs the guard deciding whether a reversal
        is permitted at all; a backend applies the plan and does not second-
        guess it. If any step fails, the whole operation is rolled back.

        Implementations must refuse rather than proceed if `survivor` still has
        edges after `delete_edge_ids` is applied. Reaching that state means the
        guard let something through, and a silent drop would destroy exactly
        what the guard exists to protect.
        """
        ...

    async def write_batch_tx(
        self,
        *,
        nodes: Sequence[EpistemicNode] = (),
        edges: Sequence[NodeEdge] = (),
        embeddings: Sequence[EmbeddingRecord] = (),
        timelines: Sequence[Timeline] = (),
    ) -> None:
        """Atomically insert a batch of nodes, edges, embeddings and timelines.

        All-or-nothing (no status changes or deletes). Used by the multi-write
        paths that compute their full output in Python before persisting —
        ingest (``store_decomposition``) and parent/subtopic synthesis — so a
        mid-operation failure cannot leave a partial graph.

        Unlike the `store_*` methods, nodes, edges and embeddings are
        **insert-only, not upsert**: ids are assumed new (these are
        freshly-created records). Re-writing an existing id through this path is
        a caller error, not an update.

        **`timelines` is the exception: it upserts.** A timeline is one record
        holding a list of timepoints, so adding a timepoint is a replacement of
        that record — there is no insert-shaped way to express it. Extraction
        needs both in one batch: a `TIMELINK` edge naming a timeline that was
        never stored resolves to an empty row rather than an error, so the
        failure is silent. Rolling back an upsert therefore means restoring the
        row's *previous* content, not deleting it.

        Read-modify-write of a shared timeline is last-writer-wins across
        concurrent callers, exactly as `add_timeline_timepoint` already is.
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

    async def get_embeddings_for_items(
        self, item_ids: Sequence[str], *, model_id: str | None = None
    ) -> dict[str, list[EmbeddingRecord]]:
        """`get_embeddings_for_item` for many items in one round-trip.

        **Every requested id is a key**, mapping to `[]` when the item has no
        embeddings — including ids that are not items at all. Callers iterate
        the map, so absence must mean "you did not ask" rather than "there were
        none". Repeated ids collapse; an empty request returns `{}` without
        touching the store.

        Vectors are the heaviest rows in the store, and every phase of `reflect`
        that compares them was reading them one item at a time. Backends must
        answer in a bounded number of statements.
        """
        ...

    async def vector_search(
        self,
        query_vector: list[float],
        model_id: str,
        *,
        k: int = 10,
        node_type: NodeType | None = None,
        statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
    ) -> Sequence[tuple[str, float]]:
        """Find the k nearest items by vector similarity.

        Returns a sequence of (item_id, similarity_score) pairs,
        ordered by descending similarity.

        `statuses` says which nodes may be nominated, and the default preserves
        the guard this method used to enforce by construction: only ACTIVE nodes
        come back, so nothing resurfaces a claim the graph has retired.

        It is a *set* because recurrence needs two at once (#53 T2). A claim
        retired as HISTORICAL becoming true again can only be judged if the
        historical twin is nominated beside the active candidates — otherwise
        nobody is ever asked, and ingest quietly writes a second node saying
        what the first one said. Passing `{ACTIVE, HISTORICAL}` is what makes
        the `recurs` verdict reachable at all.

        **CORRECTED is never a sensible member.** A node retired for being
        wrong has no route back, so nominating it invites a verdict that cannot
        be recorded. Nothing here refuses it — a caller auditing corrections has
        a real use — but no retrieval path should ask for it.

        `k` counts results the caller can use, not rows examined, so a backend
        must not truncate before filtering.
        """
        ...

    # --- Lexical search ---

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
        """Ids and BM25 scores for documents matching ANY term, best first.

        The counterpart to `vector_search`, and it fails in the opposite
        direction: cosine similarity has no notion of term rarity, so a node
        reachable only by a rare token — a ticket id, an error code — is
        unreachable today (`dev-docs/LEXICAL_SEARCH.md` §1). IDF is exactly the
        missing statistic.

        **A term is a conjunction; terms are ORed.** `"JIRA-4417"` analyzes to
        several tokens and matches only documents containing all of them, which
        is what separates it from `JIRA-4418`; a term absent from the corpus
        contributes nothing rather than excluding everything. Terms arrive
        pre-split so both backends agree on what the terms *are* before they can
        disagree about anything else.

        **`verify_containment` splits the hits by adjacency**, for callers whose
        terms were *declared* rather than guessed at (§10, R2/R8). Candidates
        still come from the same token match — an exact-containing document
        necessarily contains the tokens, so nothing is missed and `JIRA-44170`
        is still excluded before any string is compared — and are then
        partitioned by whether they hold a term literally, up to the analyzer's
        folding and without stemming (`bm25.contains_term`). Containing
        documents rank first and are **exempt from the zero rule**: their
        evidence is the containment, not the score, so a term whose common half
        sits at the IDF floor still returns the document that spells it out.
        Score orders each half internally, and the rest of the hits are
        truncated at `> 0` as always. Off by default: the fallback path infers
        its terms from the query and has no exactness to verify.

        **Scores are strictly positive** — unless `verify_containment` rescued
        a hit, whose score is reported as measured and may be zero or, on an
        engine that does not clamp its IDF, negative. Otherwise a match whose
        BM25 score is `0.0` — the IDF floor, which a term more common than half
        the corpus falls to — is not a result. Both backends enforce this; it is part of the contract,
        not an optimisation. The floor zeroes the *score*, not the *match*, and
        a zero-scored row surviving into rank fusion would arrive at an
        arbitrary tie rank and fuse almost as strongly as the best real hit
        (§10, R1). One backend scores below the floor negatively rather than
        clamping, so the rule is `> 0` and not `!= 0`.

        **One call scores one corpus partition.** With `corpus="nodes"`,
        `node_type` is required: node tables carry their own BM25 statistics, so
        IDF's N is per type and a merged multi-type list would sort incomparable
        numbers. Measured: the same term with the same hit count scores `0.0` in
        a 4-row table and `0.9615` in a 10-row one. The caller makes one call
        per type and fuses *ranks*, which are the only thing that may cross a
        table boundary (§10, R5/R6). Raises `ValueError` if `node_type` is
        omitted for the node corpus.

        `statuses` mirrors `vector_search` exactly — same default, same meaning,
        same plurality — so the two seed routes cannot disagree about whether a
        node exists. Without it a CORRECTED node — a claim concluded *wrong* —
        comes back as a lexical seed, ranked high precisely when it holds a rare
        identifier (§10, R7). Ignored for `corpus="segments"`; segments have no
        status.

        It was singular until #53 T3 made retrieval return historical nodes by
        default. A hybrid search asks both routes with one set, and had this
        stayed singular the lexical half would have been the one that could not
        see them — a node reachable only by a rare identifier would have gone
        missing exactly when the caller asked for history.

        Filtering by status does **not** change the corpus IDF is computed over.
        The index counts every row in the table whatever its status, so scores
        drift slightly as nodes retire — harmless under rank fusion, and stated
        here so nobody chases it as a bug. A backend that scored only the rows
        it was about to return would compute different numbers from the same
        graph.
        """
        ...

    async def get_nodes_by_source(
        self, source_ids: Sequence[str]
    ) -> dict[str, list[EpistemicNode]]:
        """Nodes extracted from each segment, keyed by the segment's id.

        The bridge a segment hit crosses to reach the graph. `store_decomposition`
        is agent-driven, so a fact written as "the deployment ticket was closed"
        never contains the identifier that was in the source text — and no search
        of any kind recovers it from nodes. The segment kept the raw text, and a
        node's `source_id` is its `Segment.id`, so a segment hit answers *the id
        is in this passage; here is what we concluded from it*
        (`LEXICAL_SEARCH.md` §1.1).

        **Every requested id is a key**, mapping to `[]` where nothing was
        extracted from that segment — including ids that are not segments at
        all. Callers iterate the map, so absence must mean "you did not ask"
        rather than "there were none", exactly as in `get_edges_for`. Repeated
        ids collapse; an empty request returns `{}` without touching the store.

        Nodes come back at **any status**; the caller applies the same gate it
        applies to direct lexical seeds, or the bridge is a side door around it
        (§10, R7).

        Batched — a bounded number of statements, not one per id.
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

    async def get_merge_overrides(self) -> MergeOverrides:
        """The active graph's own merge settings, where it has set any.

        Stored beside the reflect counter and scoped the same way, so a graph
        carries its own answers across restarts. Every field defaults to `None`,
        meaning *follow the process default*; a graph that has never been
        configured returns an all-`None` record rather than raising.
        """
        ...

    async def set_merge_overrides(self, overrides: MergeOverrides) -> None:
        """Replace the active graph's merge overrides.

        Whole-record rather than per-field, so "clear this one" and "leave this
        one alone" cannot be confused: the caller reads, edits and writes back.
        A `None` field clears that override.
        """
        ...

    async def get_require_judge(self) -> bool | None:
        """This graph's own answer to *must a write name a judge?*, or None.

        Stored beside the reflect counter and scoped the same way. `None` means
        the graph follows the process default (`EPIMEMER_REQUIRE_JUDGE`).

        **Off is the default, and blank means unknown either way** (§3.3). For
        many graphs it does not matter who judged; for others the user wants
        every write tied to an agent or a person, and this is where they say so.
        Turning it on is not retroactive: the rows that had no judge still have
        none, and still mean *unknown*.
        """
        ...

    async def set_require_judge(self, required: bool | None) -> None:
        """Set this graph's answer, or clear it with None.

        Never reachable from an MCP tool. `configure_reflection` and
        `configure_merge` are agent-callable because they tune how eagerly the
        system nominates things; this is a gate on the agent itself, and a gate
        the agent can open is decoration. Same channel as the approved-id list:
        an environment variable read at connect, and a CLI subcommand.
        """
        ...

    # --- Agents (REVIEW_MODE.md §2) ---

    async def get_agent(self, agent_id: str) -> Agent | None:
        """The agent with this id in the active graph, or None.

        Agents live in their own per-graph table rather than as graph nodes:
        as nodes they would surface in `search` and be swept by `reflect`, and
        two agents with similar descriptions are not a topic to merge (§2.5).
        """
        ...

    async def upsert_agent(self, agent: Agent) -> None:
        """Write the agent whole, replacing any record under the same id.

        Whole-record for `set_merge_overrides`' reason: the caller reads, edits
        and writes back, so *append a description* and *leave the descriptions
        alone* cannot be confused. The append-only rule lives in
        `with_description`, not here — a backend does not get to hold a policy
        the other backend could hold differently.
        """
        ...

    async def list_agents(self) -> list[Agent]:
        """Every agent in the active graph. Unordered."""
        ...

    async def get_approved_agent_ids(self) -> list[str]:
        """The ids the **user** has admitted to this graph.

        Stored beside the reflect counter and scoped the same way, so a graph
        carries its own answer across restarts. A graph nobody has configured
        returns an empty list, which refuses every claim — that is the intended
        reading, not a gap: an agent that could admit its own id would be
        asserting its own identity, and *"a different agent reviewed this"*
        would be self-asserted again (§2.2).

        Approval is **per graph**, so a judge approved for one graph is not
        approved for the next; `use_graph` re-validates for that reason.
        """
        ...

    async def set_approved_agent_ids(self, ids: list[str]) -> None:
        """Replace the active graph's approved-id list.

        Never reachable from an MCP tool. The two writers are the elicitation
        path, whose answer came from the user through their own client, and the
        `epimemer agents confirm` CLI (§2.3) — plus config seeding at connect,
        which is the same user saying it a third way.
        """
        ...

    # --- The decision journal (REVIEW_MODE.md §4) ---

    async def record_decision(self, record: DecisionRecord) -> str:
        """Append one judgment to the active graph's journal. Returns its id.

        **Append is the only write.** There is no update and no delete on this
        protocol, and that absence is the design rather than an omission: a
        reversal, a confirmation and an overturn are all new rows pointing back
        at old ones (§4). A backend that offered an edit would let review state
        become mutable in one place and derived in another, which is the shape
        #54, #55 and #56 all were.

        Written *after* the decision it records has succeeded, so a refused
        write leaves no row. The two are not one transaction: a lost row costs
        the journal an entry, while a row for a write that never landed would
        have review chasing a decision the graph never made.
        """
        ...

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        """One journal row by id, or None."""
        ...

    async def query_decisions(
        self,
        *,
        agent_id: str | None = None,
        kinds: Sequence[DecisionKind] | None = None,
        subject_id: str | None = None,
        reviews: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[DecisionRecord]:
        """Journal rows matching every filter given, newest first.

        The four reads review mode needs (§10.5), in one method because they
        compose: *"what did agent-1 decide yesterday"* is two of them, and a
        method per filter would make the composition the caller's problem.

        `agent_id` matches `judged_by.agent_id` and never the digest — a judge
        that re-described itself is the same judge, which is the whole reason
        the id is assigned rather than derived from the description (§2.1).
        Rows with no judge match no `agent_id`, since unknown is not an id.

        `since` is inclusive and `until` exclusive, the half-open convention
        `query_changes` already uses, so adjacent windows neither overlap nor
        drop a row on the boundary.

        `limit` truncates in **journal order** — newest first — which is not the
        order review presents results in (§6.2). A caller that re-sorts has to
        fetch enough rows to sort, and the cap it reports is its own.
        """
        ...

    async def reviewed_decision_ids(self, decision_ids: Sequence[str]) -> set[str]:
        """Which of these ids some other row `reviews`.

        The batch form of *"has anyone checked this"*, which is what makes
        `unreviewed` derived from existence rather than stored as a flag (§3.4).
        One query for a page of results rather than one per row.
        """
        ...

    async def count_decisions_by_graph(
        self,
        databases: Sequence[str],
        *,
        agent_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, int]:
        """How many journal rows each named graph holds — counts and nothing else.

        The locator behind `review()`'s `elsewhere` (#73). A reviewer checking a
        *later, different* agent's work does not know which graphs that agent
        worked in; the agent that made the decisions switched them itself and
        never needed telling.

        **A count, deliberately, and never a row.** `subject_ids` are node ids,
        and a node id resolves only in the graph that holds it — so rows read
        out of another graph would arrive readable but not actionable, and every
        write path is single-graph anyway (#72). What a reviewer needs from here
        is *where else to look*; the looking is `use_graph` then `review()`,
        which stays the fallback and is not a workaround.

        **A locator may overcount and must never undercount.** Only the filters
        `query_decisions` already implements are mirrored here — `agent_id`,
        `since`, `until` — and the narrowings a reviewer applies while browsing
        (`certainty_ceiling`, `mode="unreviewed"`) are not. Every filter
        reimplemented for a second read is somewhere the two can disagree, and a
        locator that disagrees with the reader it points at is worse than one
        that is plainly wider: too high sends a reviewer to look and find less,
        too low leaves them not looking at all.

        A graph that cannot be read — deleted between listing and counting — is
        **omitted** rather than counted zero, because the caller reports the
        difference and *nothing there* is not *not checked*.

        Naming a graph must not create it. On SurrealDB the read borrows the
        connection and takes the guard's mover turn for the whole sweep, so the
        counts are of one instant and nothing else runs during it (#16); the
        in-memory backend indexes a dict and borrows nothing.
        """
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

    @property
    def graph_guard(self) -> GraphGuard:
        """Keeps the active graph still while a logical operation uses it.

        Every backend has one and none of them may skip it: the active graph is
        process state everywhere, so a switch landing mid-operation sends the
        rest of that operation to another graph on the in-memory backend as
        surely as on SurrealDB (#16).

        Callers take `using()`; the two things that move the graph —
        `switch_database` and the `viz_list_*` borrow — take `moving()`
        themselves. See `storage/active_graph.py` for why the boundary is the
        logical operation rather than the query.
        """
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
