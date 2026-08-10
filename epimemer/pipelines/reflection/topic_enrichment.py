"""Topic enrichment for the reflection layer.

Identifies topics whose associated material has grown substantially
richer than their current description, and gathers that material so the
calling agent can re-synthesize the description through `apply_reflection`.
"""

from typing import Sequence

from epimemer.core.types import (
    EdgeType,
    Fact,
    Inference,
    Topic,
)
from epimemer.storage.protocol import StorageBackend


async def gather_associated_material_for(
    topics: Sequence[Topic], storage: StorageBackend
) -> dict[str, list[str]]:
    """Text content of the epistemic nodes linked to each topic, keyed by id.

    Gathers content from:
    - Facts via incoming SUPPORTS edges (fact → topic)
    - Inferences via incoming ABSTRACTS edges (inference → topic)

    Two queries for the whole topic set rather than two per topic — both
    `reflect` phases that need material walk every active topic, so per-topic
    reads made this one of the larger N+1 sites (ISSUES.md #14).
    """
    topic_ids = [topic.id for topic in topics]
    by_edge_type = {
        edge_type: await storage.get_edges_for(
            topic_ids, direction="to", edge_type=edge_type
        )
        for edge_type in (EdgeType.SUPPORTS, EdgeType.ABSTRACTS)
    }

    material: dict[str, list[str]] = {}
    for topic_id in topic_ids:
        contents: list[str] = []
        for edges in by_edge_type.values():
            for edge in edges[topic_id]:
                node = await storage.get_node(edge.src_id)
                if node is not None and isinstance(node, (Fact, Inference)):
                    contents.append(node.content)
        material[topic_id] = contents
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


