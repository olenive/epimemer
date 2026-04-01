"""Topic hierarchy utilities for the DAG of SUBTOPIC_OF relationships.

Provides traversal, cycle detection, and query functions over the
topic hierarchy stored as SUBTOPIC_OF edges in the graph.
"""

from typing import Sequence

from epimemer.core.types import EdgeType, EpistemicNode, NodeEdge, NodeType
from epimemer.storage.protocol import StorageBackend


async def would_create_cycle(
    storage: StorageBackend, child_id: str, proposed_parent_id: str
) -> bool:
    """Check if adding a child -> proposed_parent SUBTOPIC_OF edge would create a cycle.

    Walks ancestors of proposed_parent to see if child_id is reachable.
    """
    if child_id == proposed_parent_id:
        return True
    visited: set[str] = set()
    stack = [proposed_parent_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        parent_edges = await storage.get_edges_from(current, edge_type=EdgeType.SUBTOPIC_OF)
        for edge in parent_edges:
            if edge.dst_id == child_id:
                return True
            stack.append(edge.dst_id)
    return False


async def get_children(
    storage: StorageBackend, topic_id: str
) -> Sequence[EpistemicNode]:
    """Get all direct child topics of a given topic."""
    edges = await storage.get_edges_to(topic_id, edge_type=EdgeType.SUBTOPIC_OF)
    children = []
    for edge in edges:
        node = await storage.get_node(edge.src_id)
        if node is not None:
            children.append(node)
    return children


async def get_parents(
    storage: StorageBackend, topic_id: str
) -> Sequence[EpistemicNode]:
    """Get all direct parent topics of a given topic."""
    edges = await storage.get_edges_from(topic_id, edge_type=EdgeType.SUBTOPIC_OF)
    parents = []
    for edge in edges:
        node = await storage.get_node(edge.dst_id)
        if node is not None:
            parents.append(node)
    return parents


async def get_roots(storage: StorageBackend) -> Sequence[EpistemicNode]:
    """Get all root topics (topics with no SUBTOPIC_OF edges pointing outward)."""
    all_topics = await storage.query_nodes(node_type=NodeType.TOPIC)
    roots = []
    for topic in all_topics:
        parent_edges = await storage.get_edges_from(topic.id, edge_type=EdgeType.SUBTOPIC_OF)
        if not parent_edges:
            roots.append(topic)
    return roots


async def get_descendants(
    storage: StorageBackend, topic_id: str
) -> Sequence[EpistemicNode]:
    """Get all descendants of a topic (transitive children), excluding the topic itself."""
    visited: set[str] = set()
    result: list[EpistemicNode] = []
    stack = [topic_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        child_edges = await storage.get_edges_to(current, edge_type=EdgeType.SUBTOPIC_OF)
        for edge in child_edges:
            if edge.src_id not in visited:
                node = await storage.get_node(edge.src_id)
                if node is not None:
                    result.append(node)
                    stack.append(edge.src_id)
    return result


async def add_subtopic_edge(
    storage: StorageBackend, child_id: str, parent_id: str, weight: float = 1.0
) -> NodeEdge:
    """Create a SUBTOPIC_OF edge from child to parent, with cycle detection.

    Raises ValueError if the edge would create a cycle.
    """
    if await would_create_cycle(storage, child_id, parent_id):
        raise ValueError(
            f"Adding SUBTOPIC_OF edge from {child_id} to {parent_id} would create a cycle"
        )
    edge = NodeEdge(
        src_id=child_id,
        dst_id=parent_id,
        type=EdgeType.SUBTOPIC_OF,
        weight=weight,
    )
    await storage.store_edge(edge)
    return edge
