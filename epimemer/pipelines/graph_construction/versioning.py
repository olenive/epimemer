"""Node history and versioning functions.

Handles node supersession (creating a new version) and node merging
(combining multiple nodes into one).
"""

from datetime import datetime, timezone

from epimemer.core.types import (
    EdgeType,
    EpistemicNode,
    NodeEdge,
    NodeStatus,
    Topic,
)
from epimemer.storage.protocol import StorageBackend


async def supersede_node(
    old_node: EpistemicNode,
    new_node: EpistemicNode,
    storage: StorageBackend,
) -> NodeEdge:
    """Create a new version of a node.

    Marks the old node as superseded, stores the new node, and creates
    a superseded_by edge from old to new.

    Args:
        old_node: The node being superseded.
        new_node: The replacement node.
        storage: The storage backend.

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

    # Store the new node
    await storage.store_node(new_node)

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
