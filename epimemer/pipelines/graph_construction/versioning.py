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
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


# Version-lineage edges describe history, not knowledge. They are anchored to a
# specific node version and must NOT be migrated when a node is superseded.
_LINEAGE_EDGE_TYPES: set[EdgeType] = {EdgeType.SUPERSEDED_BY, EdgeType.MERGED_INTO}


async def _migrate_edges(
    old_id: str,
    new_id: str,
    storage: StorageBackend,
) -> int:
    """Re-point every non-lineage edge from an old node onto its replacement.

    The endpoint referencing ``old_id`` is rewritten to ``new_id`` while the
    edge's identity, type, weight, and metadata are preserved. Version-lineage
    edges are left on the old node. Returns the number of edges migrated.
    """
    outgoing = await storage.get_edges_from(old_id)
    incoming = await storage.get_edges_to(old_id)

    migrated = 0
    seen: set[str] = set()
    for edge in [*outgoing, *incoming]:
        if edge.id in seen:
            continue
        seen.add(edge.id)
        if edge.type in _LINEAGE_EDGE_TYPES:
            continue

        new_src = new_id if edge.src_id == old_id else edge.src_id
        new_dst = new_id if edge.dst_id == old_id else edge.dst_id
        if new_src == edge.src_id and new_dst == edge.dst_id:
            continue

        repointed = edge.model_copy(update={"src_id": new_src, "dst_id": new_dst})
        await storage.delete_edge(edge.id)
        await storage.store_edge(repointed)
        migrated += 1

    return migrated


async def supersede_node(
    old_node: EpistemicNode,
    new_node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> NodeEdge:
    """Create a new version of a node.

    Marks the old node as superseded, stores and embeds the new node, migrates
    the old node's relationships onto the replacement, and creates a
    superseded_by edge from old to new.

    Embedding and edge migration are done here so that *every* supersession path
    is correct by construction: a replacement that is not embedded is invisible
    to vector search, and one that does not inherit its predecessor's edges is
    orphaned from its supporting evidence.

    Args:
        old_node: The node being superseded.
        new_node: The replacement node.
        storage: The storage backend.
        embedding_provider: Used to embed the replacement node's content.

    Returns:
        The superseded_by edge linking old to new.
    """
    now = datetime.now(timezone.utc)

    # Mark old node as superseded
    await storage.update_node_status(
        node_id=old_node.id,
        status=NodeStatus.SUPERSEDED,
        superseded_at=now,
    )

    # Store the new node and index it so search can return the correction
    await storage.store_node(new_node)
    vectors = await embedding_provider.embed([new_node.content])
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=new_node.id,
            model_id=embedding_provider.model_id,
            vector=vectors[0],
        )
    )

    # Inherit the old node's relationships (supporting facts, provenance,
    # metacontexts, ...) — but not its version lineage.
    await _migrate_edges(old_node.id, new_node.id, storage)

    # Create superseded_by edge
    edge = NodeEdge(
        src_id=old_node.id,
        dst_id=new_node.id,
        type=EdgeType.SUPERSEDED_BY,
    )
    await storage.store_edge(edge)

    return edge


async def merge_nodes(
    source_nodes: list[EpistemicNode],
    merged_node: EpistemicNode,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Merge multiple nodes into one.

    Marks all source nodes as merged, stores the merged node, and creates
    merged_into edges from each source to the merged node.

    Args:
        source_nodes: The nodes being merged.
        merged_node: The new combined node.
        storage: The storage backend.

    Returns:
        A list of merged_into edges, one per source node.
    """
    now = datetime.now(timezone.utc)

    # Store the merged node
    await storage.store_node(merged_node)

    edges: list[NodeEdge] = []
    for source in source_nodes:
        # Mark source as merged
        await storage.update_node_status(
            node_id=source.id,
            status=NodeStatus.MERGED,
            superseded_at=now,
        )

        # Create merged_into edge
        edge = NodeEdge(
            src_id=source.id,
            dst_id=merged_node.id,
            type=EdgeType.MERGED_INTO,
        )
        await storage.store_edge(edge)
        edges.append(edge)

    return edges


async def group_into_parent(
    children: list[Topic],
    parent: Topic,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Group child topics under a parent topic via SUBTOPIC_OF edges.

    Unlike merge_nodes, children remain active — they are not superseded.
    The parent is stored and SUBTOPIC_OF edges are created from each child
    to the parent.

    Args:
        children: The child topics to group.
        parent: The parent topic (will be stored).
        storage: The storage backend.

    Returns:
        A list of SUBTOPIC_OF edges, one per child.
    """
    from epimemer.pipelines.reflection.topic_hierarchy import would_create_cycle

    await storage.store_node(parent)

    edges: list[NodeEdge] = []
    for child in children:
        if await would_create_cycle(storage, child.id, parent.id):
            continue
        edge = NodeEdge(
            src_id=child.id,
            dst_id=parent.id,
            type=EdgeType.SUBTOPIC_OF,
        )
        await storage.store_edge(edge)
        edges.append(edge)

    return edges
