"""Hybrid topic assignment: vector-first, LLM-fallback.

Assigns topics to a segment using a two-phase strategy:
1. Embed the segment and compare to existing topic embeddings (vector search).
2. If max similarity exceeds the threshold, assign to the existing topic.
3. Otherwise, fall back to the LLM to generate a new topic description.

Over time, as the topic graph grows, most segments match existing topics
and LLM calls decrease.
"""

from epimemer.core.types import (
    EmbeddingRecord,
    NodeType,
    Segment,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.llm.protocol import DecompositionProvider
from epimemer.storage.protocol import StorageBackend


async def assign_topics(
    segment: Segment,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    storage: StorageBackend,
    similarity_threshold: float = 0.75,
) -> list[Topic]:
    """Assign topics to a segment. Returns existing or new topics.

    Strategy:
        1. Embed the segment text.
        2. Vector search for existing topic embeddings above threshold.
        3. If matches found, return the existing Topic objects.
        4. If no match, use the LLM to generate new topic(s), store them
           along with their embeddings, and return them.

    Args:
        segment: The segment to assign topics to.
        embedding_provider: Provider for computing embeddings.
        decomposition_provider: LLM provider for topic extraction fallback.
        storage: Storage backend for querying existing topics and embeddings.
        similarity_threshold: Minimum cosine similarity to match an existing topic.

    Returns:
        A list of Topic objects (existing matches or newly created).
    """
    # Step 1: Embed the segment
    vectors = await embedding_provider.embed([segment.text])
    segment_vector = vectors[0]

    # Step 2: Search for similar existing topic embeddings
    matches = await storage.vector_search(
        query_vector=segment_vector,
        model_id=embedding_provider.model_id,
        k=10,
        node_type=NodeType.TOPIC,
    )

    # Step 3: Filter by threshold and collect matching topics
    matched_topics: list[Topic] = []
    for item_id, similarity in matches:
        if similarity >= similarity_threshold:
            node = await storage.get_node(item_id)
            if node is not None and isinstance(node, Topic):
                matched_topics.append(node)

    if matched_topics:
        # Assign novelty lower for matched (existing) topics
        for topic in matched_topics:
            topic.value = ValueSignal(
                novelty=max(0.0, topic.value.novelty - 0.2),
                confidence=topic.value.confidence,
                relevance=min(1.0, topic.value.relevance + 0.1),
                last_reinforced=topic.value.last_reinforced,
            )
        return matched_topics

    # Step 4: LLM fallback — generate new topic(s)
    new_topics = await decomposition_provider.extract_topics(segment)

    for topic in new_topics:
        # Set high novelty for genuinely new topics
        topic.value = ValueSignal(
            novelty=1.0,
            confidence=topic.value.confidence,
            relevance=topic.value.relevance,
        )
        # Store the new topic
        await storage.store_node(topic)

        # Embed and store the topic embedding for future matching
        topic_vectors = await embedding_provider.embed([topic.content])
        embedding_record = EmbeddingRecord(
            item_id=topic.id,
            model_id=embedding_provider.model_id,
            vector=topic_vectors[0],
        )
        await storage.store_embedding(embedding_record)

    return new_topics
