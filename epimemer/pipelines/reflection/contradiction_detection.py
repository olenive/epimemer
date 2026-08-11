"""Contradiction detection for the reflection layer.

Finds pairs of active facts with high embedding similarity that may
represent contradictions or redundancies, for further analysis.

This phase is the one that fails first as a graph grows (#39). Pairs are
quadratic in facts and there is no redundancy left to remove — the comparisons
are genuine work — so the fix was to do that work in bulk rather than less of
it. The scoring itself now lives in `pair_scoring`, shared with the topic phase
(#47); this module keeps the part that is about facts.
"""

from epimemer.core.types import (
    EdgeType,
    Fact,
    NodeType,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.reflection.pair_scoring import (
    SCORE_BLOCK,
    similar_pairs,
    stack_uniform_width,
)
from epimemer.storage.protocol import StorageBackend

__all__ = [
    "SCORE_BLOCK",
    "detect_contradictions",
    "similar_pairs",
]


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

    # One fetch for every fact's vector, on the same terms as the edge reads
    # below: this walks the whole active fact set, so asking per fact made the
    # phase round-trip bound on a networked backend (#14).
    by_item = await storage.get_embeddings_for_items(
        [fact.id for fact in facts], model_id=effective_model_id
    )
    fact_vectors: dict[str, list[float]] = {
        fact.id: by_item[fact.id][0].vector for fact in facts if by_item[fact.id]
    }

    # Build a set of already-linked pairs (by SIMILARITY or CONTRADICTION edges).
    # Four queries for the whole fact set: the edge type is part of the query
    # rather than a filter over every edge each fact has.
    already_linked: set[frozenset[str]] = set()
    fact_ids = [fact.id for fact in facts]
    for edge_type in (EdgeType.SIMILARITY, EdgeType.CONTRADICTION):
        for direction in ("from", "to"):
            found = await storage.get_edges_for(
                fact_ids, direction=direction, edge_type=edge_type
            )
            for edges in found.values():
                for edge in edges:
                    already_linked.add(frozenset({edge.src_id, edge.dst_id}))

    # Score every pair at once, over the facts whose vectors can form a matrix.
    by_id = {fact.id: fact for fact in facts}
    kept_ids, vectors = stack_uniform_width([fact.id for fact in facts], fact_vectors)
    if not kept_ids:
        return []
    fact_list = [by_id[fact_id] for fact_id in kept_ids]

    pairs: list[tuple[Fact, Fact, float]] = []
    for i, j, similarity in similar_pairs(vectors, similarity_threshold):
        a = fact_list[i]
        b = fact_list[j]
        if frozenset({a.id, b.id}) in already_linked:
            continue
        pairs.append((a, b, similarity))

    # Sort by similarity descending. Stable, so pairs that tie stay in index
    # order — which is the order `similar_pairs` returns them in.
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs
