"""Topic enrichment for the reflection layer.

Identifies topics whose associated material has grown substantially
richer than their current description, and re-synthesizes the
description via LLM to incorporate the new detail.
"""

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    NodeType,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.llm.protocol import DecompositionProvider
from epimemer.pipelines.graph_construction.versioning import supersede_node
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


async def enrich_stale_topics(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    *,
    material_ratio: float = 3.0,
    model_id: str | None = None,
) -> list[tuple[Topic, Topic]]:
    """Find topics with rich material but thin descriptions, and re-synthesize.

    For each active topic, gathers linked segments and facts. If the
    combined material is substantially richer than the topic description
    (controlled by material_ratio), asks the LLM to produce an improved
    description. The old topic is superseded by the enriched version.

    Args:
        storage: The storage backend.
        embedding_provider: For embedding the enriched topic.
        decomposition_provider: LLM provider for re-synthesis.
        material_ratio: Minimum ratio of material length to description
            length before enrichment triggers. Default 3.0 means material
            must be at least 3x the description length.
        model_id: Override the model_id for embedding lookup/storage.

    Returns:
        A list of (old_topic, enriched_topic) pairs that were updated.
    """
    effective_model_id = model_id or embedding_provider.model_id
    all_topics = await storage.query_nodes(node_type=NodeType.TOPIC)
    topics = [t for t in all_topics if isinstance(t, Topic)]

    enriched_pairs: list[tuple[Topic, Topic]] = []

    for topic in topics:
        material = await gather_associated_material(topic, storage)
        if not _should_enrich(topic, material, material_ratio):
            continue

        new_content = await decomposition_provider.enrich_topic_description(
            topic, material
        )

        # Skip if the LLM returned essentially the same content
        if new_content.strip() == topic.content.strip():
            continue

        enriched_topic = Topic(
            content=new_content,
            source_id=topic.source_id,
            value=topic.value,
            extraction_method=f"{topic.extraction_method}:enriched",
            metadata={**topic.metadata, "enriched_from": topic.id},
        )

        await supersede_node(topic, enriched_topic, storage)

        # Embed the enriched topic
        vectors = await embedding_provider.embed([enriched_topic.content])
        await storage.store_embedding(
            EmbeddingRecord(
                item_id=enriched_topic.id,
                model_id=effective_model_id,
                vector=vectors[0],
            )
        )

        enriched_pairs.append((topic, enriched_topic))

    return enriched_pairs
