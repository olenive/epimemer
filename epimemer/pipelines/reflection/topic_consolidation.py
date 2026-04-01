"""Topic consolidation for the reflection layer.

Provides both flat merging (original behavior, marks sources as merged)
and hierarchical consolidation (groups similar topics under a synthesized
parent, keeping children active).
"""

import math

from epimemer.core.types import (
    EmbeddingRecord,
    NodeType,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.llm.protocol import DecompositionProvider
from epimemer.pipelines.graph_construction.versioning import group_into_parent, merge_nodes
from epimemer.storage.protocol import StorageBackend


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


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
) -> Topic:
    """Merge two similar topics into one.

    Creates a new Topic with combined content from both sources. The topic
    with higher confidence is used as the primary content, with the other
    appended. Value signals are combined (max confidence, max relevance,
    average novelty).

    Uses merge_nodes from the versioning module to mark originals as merged
    and create merged_into edges.

    Args:
        topic_a: First topic to merge.
        topic_b: Second topic to merge.
        storage: The storage backend.

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

    # Use merge_nodes to handle status updates, storage, and edge creation
    await merge_nodes(
        source_nodes=[topic_a, topic_b],
        merged_node=merged_topic,
        storage=storage,
    )

    return merged_topic


async def consolidate_as_hierarchy(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    *,
    similarity_threshold: float = 0.85,
    model_id: str | None = None,
    min_group_size: int = 2,
) -> list[Topic]:
    """Find clusters of similar topics and group them under synthesized parents.

    Unlike merge_similar_topics, children remain active — they become
    subtopics of a new parent via SUBTOPIC_OF edges.

    Groups are formed by collecting connected components from the pairwise
    similarity graph (pairs above threshold). Each group of min_group_size
    or more gets a parent synthesized by the LLM.

    Args:
        storage: The storage backend.
        embedding_provider: Provider for embedding lookups.
        decomposition_provider: LLM provider for parent synthesis.
        similarity_threshold: Minimum cosine similarity for grouping.
        model_id: Override the model_id for embedding lookup.
        min_group_size: Minimum cluster size to create a parent.

    Returns:
        A list of newly created parent topics.
    """
    pairs = await find_similar_topic_pairs(
        storage, embedding_provider,
        similarity_threshold=similarity_threshold,
        model_id=model_id,
    )

    if not pairs:
        return []

    # Build connected components from similar pairs (union-find)
    parent_map: dict[str, str] = {}

    def find(x: str) -> str:
        while parent_map.get(x, x) != x:
            parent_map[x] = parent_map.get(parent_map[x], parent_map[x])
            x = parent_map[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent_map[ra] = rb

    topic_by_id: dict[str, Topic] = {}
    for topic_a, topic_b, _score in pairs:
        topic_by_id[topic_a.id] = topic_a
        topic_by_id[topic_b.id] = topic_b
        union(topic_a.id, topic_b.id)

    # Collect groups
    groups: dict[str, list[Topic]] = {}
    for topic_id, topic in topic_by_id.items():
        root = find(topic_id)
        groups.setdefault(root, []).append(topic)

    # For each group, synthesize a parent and create hierarchy
    effective_model_id = model_id or embedding_provider.model_id
    created_parents: list[Topic] = []

    for group in groups.values():
        if len(group) < min_group_size:
            continue

        parent_topic = await decomposition_provider.synthesize_parent_topic(group)
        await group_into_parent(group, parent_topic, storage)

        # Embed the parent for future similarity searches
        parent_vectors = await embedding_provider.embed([parent_topic.content])
        await storage.store_embedding(
            EmbeddingRecord(
                item_id=parent_topic.id,
                model_id=effective_model_id,
                vector=parent_vectors[0],
            )
        )

        created_parents.append(parent_topic)

    return created_parents
