"""Topic consolidation for the reflection layer.

Provides both flat merging (original behavior, marks sources as merged)
and hierarchical consolidation (groups similar topics under a synthesized
parent, keeping children active).
"""

import math

from epimemer.core.types import (
    EpistemicNode,
    NodeType,
    Topic,
    merged_value_signal,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.graph_construction.versioning import merge_nodes
from epimemer.pipelines.reflection.pair_scoring import (
    similar_pairs,
    stack_uniform_width,
)
from epimemer.storage.protocol import StorageBackend


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors.

    Kept scalar deliberately. Its one remaining caller is the merge safety bar,
    which runs over the two or three nodes of a single proposed merge — bounded
    by the merge, not by the graph — so the setup cost of a matrix product would
    exceed the comparisons it replaces. The graph-scaled loop that used to be
    here is now `similar_pairs` (#47).
    """
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
    by_item = await storage.get_embeddings_for_items(
        [node.id for node in nodes], model_id=model_id
    )
    vectors: list[list[float]] = []
    for node in nodes:
        embeddings = by_item[node.id]
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

    # One fetch for every topic's vector rather than one per topic: a vector is
    # the most expensive row in the store, and this walks all of them (#14).
    by_item = await storage.get_embeddings_for_items(
        [topic.id for topic in topics], model_id=effective_model_id
    )
    topic_vectors: dict[str, list[float]] = {
        topic.id: by_item[topic.id][0].vector
        for topic in topics
        if by_item[topic.id]
    }

    # Score every pair at once, over the topics whose vectors can form a matrix.
    # This was the last per-pair Python loop in `reflect`, and once storage
    # stopped being the cost it was most of the wall clock: 71% on SurrealDB and
    # 88% in-memory at 1,200 nodes, against 9 ms of storage (#47).
    by_id = {topic.id: topic for topic in topics}
    kept_ids, vectors = stack_uniform_width(
        [topic.id for topic in topics], topic_vectors
    )
    if not kept_ids:
        return []
    topic_list = [by_id[topic_id] for topic_id in kept_ids]

    pairs = [
        (topic_list[i], topic_list[j], similarity)
        for i, j, similarity in similar_pairs(vectors, similarity_threshold)
    ]

    # Sort by similarity descending. Stable, so pairs that tie stay in index
    # order — which is the order `similar_pairs` returns them in, and the order
    # the loop this replaced produced them in.
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
    appended. Value signals are combined by `merged_value_signal`, which
    carries both value clocks across as well as the scalars.

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

    # Shared with the wired merge in `apply_reflection`, and shared deliberately:
    # rebuilding a signal field by field silently resets whatever it forgets to
    # name, which is how both clocks came to be dropped here (#45).
    merged_value = merged_value_signal([topic_a.value, topic_b.value])

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


