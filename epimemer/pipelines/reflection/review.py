"""Domain helpers for the epistemic review loop (see REVIEW_EPISTEMIC.md).

Pure planning functions (reads only) that compute the edges / edge-ids a
supersession or resolution must apply atomically.
"""

from epimemer.core.types import EdgeType, Inference, NodeEdge
from epimemer.storage.protocol import StorageBackend


async def plan_evidence_stale_edges(
    superseded_fact_id: str,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Edges flagging inferences whose evidence just changed (Case B).

    When a fact is superseded, the inferences that *directly* depend on it become
    suspect. Dependents are the inferences the fact ``supports`` and those that
    are ``derived_from`` it. Returns one ``evidence_superseded`` edge
    (fact → inference) per direct dependent. Direct only — no transitive cascade
    (a dependent that is later superseded flags its own dependents in turn).
    """
    dependent_ids: list[str] = []
    seen: set[str] = set()

    # fact --supports--> inference
    for edge in await storage.get_edges_from(
        superseded_fact_id, edge_type=EdgeType.SUPPORTS
    ):
        node = await storage.get_node(edge.dst_id)
        if isinstance(node, Inference) and node.id not in seen:
            seen.add(node.id)
            dependent_ids.append(node.id)

    # inference --derived_from--> fact
    for edge in await storage.get_edges_to(
        superseded_fact_id, edge_type=EdgeType.DERIVED_FROM
    ):
        node = await storage.get_node(edge.src_id)
        if isinstance(node, Inference) and node.id not in seen:
            seen.add(node.id)
            dependent_ids.append(node.id)

    return [
        NodeEdge(
            src_id=superseded_fact_id,
            dst_id=inference_id,
            type=EdgeType.EVIDENCE_SUPERSEDED,
        )
        for inference_id in dependent_ids
    ]


async def find_candidate_edge_ids_into(
    node_id: str,
    storage: StorageBackend,
) -> list[str]:
    """Ids of ``supersession_candidate`` edges pointing at ``node_id``.

    These are cleared when the node is resolved (superseded) — the candidacy has
    been decided.
    """
    edges = await storage.get_edges_to(
        node_id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
    )
    return [edge.id for edge in edges]
