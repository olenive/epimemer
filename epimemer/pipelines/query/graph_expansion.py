"""Graph expansion for the query layer.

Performs breadth-first traversal from seed nodes to discover related
nodes and edges in the epistemic graph. Skips history edges by default.
"""

from epimemer.core.types import EdgeType, EpistemicNode, NodeEdge, traversal_excluded
from epimemer.storage.protocol import StorageBackend


async def expand_via_graph(
    seed_nodes: list[EpistemicNode],
    storage: StorageBackend,
    *,
    hops: int = 1,
    exclude_edge_types: set[EdgeType] | None = None,
) -> tuple[list[EpistemicNode], list[NodeEdge]]:
    """Expand from seed nodes by traversing graph edges.

    By default skips edges that are not knowledge to follow — history, review, and
    provenance/attribution edges (via ``traversal_excluded``) — so a search does not
    fan out from version or source hubs. An explicit ``exclude_edge_types`` set
    overrides that with plain type membership.
    """
    def _skip(edge: NodeEdge) -> bool:
        if exclude_edge_types is not None:
            return edge.type in exclude_edge_types
        return traversal_excluded(edge)

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
                # Skip edges not followed in default traversal
                if _skip(edge):
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
