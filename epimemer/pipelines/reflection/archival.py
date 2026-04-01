"""Archival functions for the reflection layer.

Identifies superseded and merged nodes that are old enough to archive,
and exports them with their history edges into a serializable format
suitable for cold storage.
"""

from datetime import datetime, timedelta, timezone

from epimemer.core.types import (
    EpistemicNode,
    NodeStatus,
)
from epimemer.storage.protocol import StorageBackend


async def find_archival_candidates(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
) -> list[EpistemicNode]:
    """Find superseded or merged nodes older than the cutoff.

    Only nodes with status SUPERSEDED or MERGED whose superseded_at
    timestamp is older than max_age_days are returned. Active nodes
    are never included.

    Args:
        storage: The storage backend.
        max_age_days: Minimum age in days since supersession/merge for archival.

    Returns:
        A list of nodes eligible for archival.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    candidates: list[EpistemicNode] = []

    # Check superseded nodes
    superseded = await storage.query_nodes(status=NodeStatus.SUPERSEDED)
    for node in superseded:
        if node.superseded_at is not None and node.superseded_at <= cutoff:
            candidates.append(node)

    # Check merged nodes
    merged = await storage.query_nodes(status=NodeStatus.MERGED)
    for node in merged:
        if node.superseded_at is not None and node.superseded_at <= cutoff:
            candidates.append(node)

    return candidates


async def archive_nodes(
    nodes: list[EpistemicNode],
    storage: StorageBackend,
) -> dict:
    """Export nodes and their history edges to a serializable dict.

    Collects each node's outgoing and incoming edges, and bundles
    them into a dictionary suitable for JSON serialization and
    cold storage. Does NOT delete anything from storage.

    Args:
        nodes: The nodes to archive.
        storage: The storage backend (for edge lookup).

    Returns:
        A dict with 'nodes' (list of node dicts) and 'edges' (list of edge dicts).
    """
    archived_nodes: list[dict] = []
    archived_edges: list[dict] = []
    seen_edge_ids: set[str] = set()

    for node in nodes:
        archived_nodes.append(node.model_dump(mode="json"))

        # Collect all edges connected to this node
        edges_from = await storage.get_edges_from(node.id)
        edges_to = await storage.get_edges_to(node.id)

        for edge in list(edges_from) + list(edges_to):
            if edge.id not in seen_edge_ids:
                seen_edge_ids.add(edge.id)
                archived_edges.append(edge.model_dump(mode="json"))

    return {
        "nodes": archived_nodes,
        "edges": archived_edges,
    }
