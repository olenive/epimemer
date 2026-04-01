"""Contradiction detection for the reflection layer.

Finds pairs of active facts with high embedding similarity that may
represent contradictions or redundancies, for further analysis.
"""

import math

from epimemer.core.types import (
    EdgeType,
    Fact,
    NodeType,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def detect_contradictions(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.80,
    model_id: str | None = None,
) -> list[tuple[Fact, Fact, float]]:
    """Find pairs of active facts with high embedding similarity.

    High semantic similarity between facts can indicate either:
    - Redundancy (same fact stated differently)
    - Contradiction (opposing claims about the same thing)

    Returns candidate pairs for further analysis. Excludes pairs already
    linked by SIMILARITY or CONTRADICTION edges.

    Args:
        storage: The storage backend.
        embedding_provider: Used to determine the model_id for embeddings.
        similarity_threshold: Minimum cosine similarity for a pair to be included.
        model_id: Override the model_id used for embedding lookup.

    Returns:
        A list of (fact_a, fact_b, similarity_score) sorted by score descending.
    """
    effective_model_id = model_id or embedding_provider.model_id

    # Get all active facts
    active_facts = await storage.query_nodes(node_type=NodeType.FACT)
    facts: list[Fact] = [f for f in active_facts if isinstance(f, Fact)]

    if len(facts) < 2:
        return []

    # Get embeddings for each fact
    fact_vectors: dict[str, list[float]] = {}
    for fact in facts:
        embeddings = await storage.get_embeddings_for_item(
            fact.id, model_id=effective_model_id
        )
        if embeddings:
            fact_vectors[fact.id] = embeddings[0].vector

    # Build a set of already-linked pairs (by SIMILARITY or CONTRADICTION edges)
    linked_edge_types = {EdgeType.SIMILARITY, EdgeType.CONTRADICTION}
    already_linked: set[frozenset[str]] = set()
    for fact in facts:
        edges_from = await storage.get_edges_from(fact.id)
        edges_to = await storage.get_edges_to(fact.id)
        for edge in list(edges_from) + list(edges_to):
            if edge.type in linked_edge_types:
                pair = frozenset({edge.src_id, edge.dst_id})
                already_linked.add(pair)

    # Compute pairwise similarities
    pairs: list[tuple[Fact, Fact, float]] = []
    fact_list = [f for f in facts if f.id in fact_vectors]

    for i in range(len(fact_list)):
        for j in range(i + 1, len(fact_list)):
            a = fact_list[i]
            b = fact_list[j]

            # Skip already-linked pairs
            if frozenset({a.id, b.id}) in already_linked:
                continue

            similarity = _cosine_similarity(
                fact_vectors[a.id], fact_vectors[b.id]
            )
            if similarity >= similarity_threshold:
                pairs.append((a, b, similarity))

    # Sort by similarity descending
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs
