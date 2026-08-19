"""Node history and versioning functions.

Handles node supersession (creating a new version) and node merging
(combining multiple nodes into one).
"""

from datetime import datetime, timezone

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    NodeEdge,
    NodeStatus,
    Topic,
    lineage_edge_type_for,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


async def supersede_node(
    old_node: EpistemicNode,
    new_node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    status: NodeStatus,
) -> NodeEdge:
    """Create a new version of a node.

    Marks the old node as superseded, stores and embeds the new node, carries
    its edges over according to `status`, and creates a lineage edge from old to
    new — all atomically (the storage backend applies it as a single
    transaction).

    Embedding and edge migration are part of the operation so that *every*
    supersession path is correct by construction: a replacement that is not
    embedded is invisible to vector search, and one that does not inherit the
    edges it is entitled to is orphaned — from its evidence after a correction,
    or from its frame and topics after a world-change.

    Args:
        old_node: The node being superseded.
        new_node: The replacement node.
        storage: The storage backend.
        embedding_provider: Used to embed the replacement node's content.
        status: Why the old node is being retired — `CORRECTED` (it was wrong)
            or `HISTORICAL` (the world changed, and it is still right of its
            period). No default: the two are opposite events and only the
            caller knows which one happened (#53). It selects two things
            besides the status itself: the edge migration policy — see
            `migration_disposition` (#54), under which a `HISTORICAL` node
            keeps its own provenance and the judgments made about it while only
            its frame and tags are copied onto the replacement — and which
            lineage edge is written, see `lineage_edge_type_for`.

    Returns:
        The lineage edge linking old to new: `superseded_by` for a correction,
        `temporally_followed_by` for a world-change.
    """
    from epimemer.pipelines.reflection.review import (
        find_candidate_edge_ids_into,
        plan_evidence_stale_edges,
    )

    now = datetime.now(timezone.utc)

    # Sources, tags, and relationships ride along via edge migration below
    # (sourced_from / tagged_with / user edges are migrated, not version-anchored).
    vectors = await embedding_provider.embed([new_node.content])
    new_embedding = EmbeddingRecord(
        item_id=new_node.id,
        model_id=embedding_provider.model_id,
        vector=vectors[0],
    )
    lineage_edge = NodeEdge(
        src_id=old_node.id,
        dst_id=new_node.id,
        type=lineage_edge_type_for(status),
    )
    # Flag dependent inferences (Case B) and clear any candidacy on the old node.
    evidence_edges = await plan_evidence_stale_edges(old_node.id, storage)
    clear_edge_ids = await find_candidate_edge_ids_into(old_node.id, storage)

    await storage.supersede_node_tx(
        old_node, new_node, new_embedding, lineage_edge,
        status=status, superseded_at=now,
        evidence_edges=evidence_edges, clear_edge_ids=clear_edge_ids,
    )
    return lineage_edge


async def supersede_by_existing(
    old_node: EpistemicNode,
    existing_id: str,
    storage: StorageBackend,
    *,
    status: NodeStatus,
) -> NodeEdge:
    """Supersede ``old_node`` by an already-existing node.

    Used where the current truth is a node that already exists (rather than
    freshly-written content) — either because the earlier claim was wrong or
    because the world changed, which ``status`` distinguishes (#53). Marks the
    old node with ``status`` and writes the lineage edge that goes with it
    (old → existing) — `superseded_by` for a correction,
    `temporally_followed_by` for a world-change, see `lineage_edge_type_for`.
    Flags dependent inferences (Case B), and clears any supersession_candidate
    edges on the old node — atomically. The existing node is unchanged: its
    evidence is its own, so the old node's edges are deliberately NOT migrated
    onto it.

    Returns the lineage edge.
    """
    from epimemer.pipelines.reflection.review import (
        find_candidate_edge_ids_into,
        plan_evidence_stale_edges,
    )

    now = datetime.now(timezone.utc)
    lineage_edge = NodeEdge(
        src_id=old_node.id,
        dst_id=existing_id,
        type=lineage_edge_type_for(status),
    )
    evidence_edges = await plan_evidence_stale_edges(old_node.id, storage)
    clear_edge_ids = await find_candidate_edge_ids_into(old_node.id, storage)

    await storage.supersede_by_existing_tx(
        old_node, existing_id, lineage_edge, status=status, superseded_at=now,
        evidence_edges=evidence_edges, clear_edge_ids=clear_edge_ids,
    )
    return lineage_edge


async def merge_nodes(
    source_nodes: list[EpistemicNode],
    merged_node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> list[NodeEdge]:
    """Merge multiple nodes into one.

    Stores and embeds the merged node, migrates the sources' relationships onto
    it, marks all sources as merged, and creates merged_into edges from each
    source to the merged node — all atomically (the storage backend applies it
    as a single transaction).

    As with supersede_node, embedding and edge migration are part of the
    operation so the merged node is searchable and inherits its sources'
    supporting evidence rather than being orphaned.

    Args:
        source_nodes: The nodes being merged.
        merged_node: The new combined node.
        storage: The storage backend.
        embedding_provider: Used to embed the merged node's content.

    Returns:
        A list of merged_into edges, one per source node.
    """
    now = datetime.now(timezone.utc)

    # The merged node inherits its sources' sources/tags/relationships via edge
    # migration below (sourced_from / tagged_with / user edges are migrated).
    vectors = await embedding_provider.embed([merged_node.content])
    merged_embedding = EmbeddingRecord(
        item_id=merged_node.id,
        model_id=embedding_provider.model_id,
        vector=vectors[0],
    )
    lineage_edges = [
        NodeEdge(src_id=source.id, dst_id=merged_node.id, type=EdgeType.MERGED_INTO)
        for source in source_nodes
    ]

    await storage.merge_nodes_tx(
        source_nodes, merged_node, merged_embedding, lineage_edges, merged_at=now,
    )
    return lineage_edges


async def plan_subtopic_edges(
    children: list[Topic],
    parent_id: str,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Plan SUBTOPIC_OF edges from children to a parent, skipping cycles.

    Pure planning — performs reads (cycle detection) only, no writes. The caller
    persists the parent, edges, and any embeddings together via
    ``write_batch_tx`` so the grouping is atomic. Unlike merge_nodes, children
    remain active (they are not superseded).

    Args:
        children: The child topics to group.
        parent_id: The id of the parent topic the children attach to.
        storage: The storage backend.

    Returns:
        A list of SUBTOPIC_OF edges (child → parent), one per non-cyclic child.
    """
    from epimemer.pipelines.reflection.topic_hierarchy import would_create_cycle

    edges: list[NodeEdge] = []
    for child in children:
        if await would_create_cycle(storage, child.id, parent_id):
            continue
        edges.append(NodeEdge(
            src_id=child.id,
            dst_id=parent_id,
            type=EdgeType.SUBTOPIC_OF,
        ))
    return edges
