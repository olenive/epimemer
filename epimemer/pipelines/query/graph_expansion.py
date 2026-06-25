"""Graph expansion for the query layer.

Performs breadth-first traversal from seed nodes to discover related
nodes and edges in the epistemic graph. Skips history edges by default.
"""

from epimemer.core.types import HISTORY_EDGE_TYPES, EdgeType, EpistemicNode, NodeEdge
from epimemer.storage.protocol import StorageBackend


_HISTORY_EDGE_TYPES = HISTORY_EDGE_TYPES


async def expand_via_graph(
    seed_nodes: list[EpistemicNode],
    storage: StorageBackend,
    *,
    hops: int = 1,
    exclude_edge_types: set[EdgeType] | None = None,
) -> tuple[list[EpistemicNode], list[NodeEdge]]:
    """Expand from seed nodes by traversing graph edges.

    Returns all discovered nodes (including seeds) and the edges between them.
    Skips history edges (SUPERSEDED_BY, MERGED_INTO) by default.
    """
    excluded = exclude_edge_types if exclude_edge_types is not None else _HISTORY_EDGE_TYPES

    seen_node_ids: set[str] = {node.id for node in seed_nodes}
    all_nodes: list[EpistemicNode] = list(seed_nodes)
    all_edges: list[NodeEdge] = []
    seen_edge_ids: set[str] = set()

    # BFS frontier: node ids to expand from in the current hop
    frontier_ids: set[str] = {node.id for node in seed_nodes}

    for _ in range(hops):
        next_frontier_ids: set[str] = set()

        for node_id in frontier_ids:
            # Get outgoing and incoming edges
            outgoing = await storage.get_edges_from(node_id)
            incoming = await storage.get_edges_to(node_id)

            for edge in list(outgoing) + list(incoming):
                # Skip excluded edge types
                if edge.type in excluded:
                    continue

                # Track edge (avoid duplicates)
                if edge.id in seen_edge_ids:
                    continue
                seen_edge_ids.add(edge.id)
                all_edges.append(edge)

                # Discover new node on the other end
                neighbor_id = edge.dst_id if edge.src_id == node_id else edge.src_id
                if neighbor_id not in seen_node_ids:
                    neighbor = await storage.get_node(neighbor_id)
                    if neighbor is not None:
                        seen_node_ids.add(neighbor_id)
                        all_nodes.append(neighbor)
                        next_frontier_ids.add(neighbor_id)

        frontier_ids = next_frontier_ids

    return all_nodes, all_edges
