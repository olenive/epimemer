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
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


async def supersede_node(
    old_node: EpistemicNode,
    new_node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> NodeEdge:
    """Create a new version of a node.

    Marks the old node as superseded, stores and embeds the new node, migrates
    the old node's relationships onto the replacement, and creates a
    superseded_by edge from old to new — all atomically (the storage backend
    applies it as a single transaction).

    Embedding and edge migration are part of the operation so that *every*
    supersession path is correct by construction: a replacement that is not
    embedded is invisible to vector search, and one that does not inherit its
    predecessor's edges is orphaned from its supporting evidence.

    Args:
        old_node: The node being superseded.
        new_node: The replacement node.
        storage: The storage backend.
        embedding_provider: Used to embed the replacement node's content.

    Returns:
        The superseded_by edge linking old to new.
    """
    now = datetime.now(timezone.utc)

    vectors = await embedding_provider.embed([new_node.content])
    new_embedding = EmbeddingRecord(
        item_id=new_node.id,
        model_id=embedding_provider.model_id,
        vector=vectors[0],
    )
    lineage_edge = NodeEdge(
        src_id=old_node.id,
        dst_id=new_node.id,
        type=EdgeType.SUPERSEDED_BY,
    )

    await storage.supersede_node_tx(
        old_node, new_node, new_embedding, lineage_edge, superseded_at=now,
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
