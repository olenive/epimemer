"""Topic enrichment for the reflection layer.

Identifies topics whose associated material has grown substantially
richer than their current description, and re-synthesizes the
description via LLM to incorporate the new detail.
"""

from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    Topic,
)
from epimemer.storage.protocol import StorageBackend


async def gather_associated_material(
    topic: Topic, storage: StorageBackend
) -> list[str]:
    """Collect text content from epistemic nodes linked to a topic.

    Gathers content from:
    - Facts via incoming SUPPORTS edges (fact → topic)
    - Inferences via incoming ABSTRACTS edges (inference → topic)
    """
    material: list[str] = []

    for edge_type in (EdgeType.SUPPORTS, EdgeType.ABSTRACTS):
        edges = await storage.get_edges_to(topic.id, edge_type=edge_type)
        for edge in edges:
            node = await storage.get_node(edge.src_id)
            if node is not None and isinstance(node, (Fact, Inference)):
                material.append(node.content)

    return material


def _should_enrich(topic: Topic, material: list[str], material_ratio: float) -> bool:
    """Determine if a topic's description is stale relative to its material.

    Returns True if the total material content is at least material_ratio
    times longer than the topic description.
    """
    if not material:
        return False
    total_material_len = sum(len(m) for m in material)
    return total_material_len >= len(topic.content) * material_ratio


