"""Topic consolidation for the reflection layer.

Provides both flat merging (original behavior, marks sources as merged)
and hierarchical consolidation (groups similar topics under a synthesized
parent, keeping children active).
"""

import math

from epimemer.core.types import (
    EmbeddingRecord,
    EpistemicNode,
    NodeType,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.graph_construction.versioning import merge_nodes
from epimemer.storage.protocol import StorageBackend


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def all_pairs_above_threshold(
    nodes: list[EpistemicNode],
    storage: StorageBackend,
    model_id: str,
    threshold: float,
) -> bool:
    """True iff every pair of nodes has stored-embedding cosine >= threshold.

    Used as a safety bar before a (consolidating) merge so that only genuine
    near-duplicates are collapsed. Returns False if any node lacks a stored
    embedding — if similarity cannot be verified, the merge is refused.
    """
    vectors: list[list[float]] = []
    for node in nodes:
        embeddings = await storage.get_embeddings_for_item(node.id, model_id=model_id)
        if not embeddings:
            return False
        vectors.append(embeddings[0].vector)

    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            if _cosine_similarity(vectors[i], vectors[j]) < threshold:
                return False
    return True


async def find_similar_topic_pairs(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.85,
    model_id: str | None = None,
) -> list[tuple[Topic, Topic, float]]:
    """Find pairs of active topics with high embedding similarity.

    Retrieves all active topics from storage, looks up their stored embeddings,
    and computes pairwise cosine similarity. Returns pairs above the threshold,
    sorted by score descending.

    Args:
        storage: The storage backend.
        embedding_provider: Used to determine the model_id for embeddings.
        similarity_threshold: Minimum cosine similarity for a pair to be included.
        model_id: Override the model_id used for embedding lookup.

    Returns:
        A list of (topic_a, topic_b, similarity_score) sorted by score descending.
    """
    effective_model_id = model_id or embedding_provider.model_id

    # Get all active topics
    active_topics = await storage.query_nodes(node_type=NodeType.TOPIC)
    topics: list[Topic] = [t for t in active_topics if isinstance(t, Topic)]

    if len(topics) < 2:
        return []

    # Get embeddings for each topic
    topic_vectors: dict[str, list[float]] = {}
    for topic in topics:
        embeddings = await storage.get_embeddings_for_item(
            topic.id, model_id=effective_model_id
        )
        if embeddings:
            topic_vectors[topic.id] = embeddings[0].vector

    # Compute pairwise similarities
    pairs: list[tuple[Topic, Topic, float]] = []
    topic_list = [t for t in topics if t.id in topic_vectors]

    for i in range(len(topic_list)):
        for j in range(i + 1, len(topic_list)):
            a = topic_list[i]
            b = topic_list[j]
            similarity = _cosine_similarity(
                topic_vectors[a.id], topic_vectors[b.id]
            )
            if similarity >= similarity_threshold:
                pairs.append((a, b, similarity))

    # Sort by similarity descending
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


async def merge_similar_topics(
    topic_a: Topic,
    topic_b: Topic,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
) -> Topic:
    """Merge two similar topics into one.

    Creates a new Topic with combined content from both sources. The topic
    with higher confidence is used as the primary content, with the other
    appended. Value signals are combined (max confidence, max relevance,
    average novelty).

    Uses merge_nodes from the versioning module to embed the merged topic,
    migrate the originals' edges onto it, mark originals as merged, and create
    merged_into edges.

    Args:
        topic_a: First topic to merge.
        topic_b: Second topic to merge.
        storage: The storage backend.
        embedding_provider: Used to embed the merged topic.

    Returns:
        The newly created merged Topic.
    """
    # Determine primary (higher confidence) and secondary
    if topic_a.value.confidence >= topic_b.value.confidence:
        primary, secondary = topic_a, topic_b
    else:
        primary, secondary = topic_b, topic_a

    # Combine content
    merged_content = f"{primary.content} {secondary.content}"

    # Combine value signals
    merged_value = ValueSignal(
        confidence=max(topic_a.value.confidence, topic_b.value.confidence),
        relevance=max(topic_a.value.relevance, topic_b.value.relevance),
        novelty=(topic_a.value.novelty + topic_b.value.novelty) / 2.0,
    )

    # Create the merged topic
    merged_topic = Topic(
        content=merged_content,
        source_id=primary.source_id,
        value=merged_value,
        extraction_method="merge",
        metadata={
            "merged_from": [topic_a.id, topic_b.id],
        },
    )

    # Use merge_nodes to handle embedding, edge migration, status updates,
    # storage, and merged_into edge creation.
    await merge_nodes(
        source_nodes=[topic_a, topic_b],
        merged_node=merged_topic,
        storage=storage,
        embedding_provider=embedding_provider,
    )

    return merged_topic


