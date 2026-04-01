"""Storage persistence for decomposed segments.

Stores all nodes and edges from a decomposed segment via the storage backend.
"""

from epimemer.core.types import NodeEdge
from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment
from epimemer.storage.protocol import StorageBackend


async def persist_decomposed_segment(
    decomposed: DecomposedSegment,
    edges: list[NodeEdge],
    storage: StorageBackend,
) -> None:
    """Store all nodes and edges from a decomposed segment.

    Persists the segment, all topics, facts, inferences, and edges
    to the storage backend.

    Args:
        decomposed: The segment bundled with its extracted nodes.
        edges: The typed edges linking the nodes.
        storage: The storage backend to persist to.
    """
    # Store the segment
    await storage.store_segment(decomposed.segment)

    # Store all epistemic nodes
    for topic in decomposed.topics:
        await storage.store_node(topic)

    for fact in decomposed.facts:
        await storage.store_node(fact)

    for inference in decomposed.inferences:
        await storage.store_node(inference)

    # Store all edges
    for edge in edges:
        await storage.store_edge(edge)
