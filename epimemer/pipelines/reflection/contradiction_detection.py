"""Contradiction detection for the reflection layer.

Finds pairs of active facts with high embedding similarity that may
represent contradictions or redundancies, for further analysis.

This phase is the one that fails first as a graph grows. Pairs are
quadratic in facts and there is no redundancy left to remove — the comparisons
are genuine work — so the fix was to do that work in bulk rather than less of
it. The scoring itself now lives in `pair_scoring`, shared with the topic phase
; this module keeps the part that is about facts.
"""

from epimemer.core.types import (
    EpistemicNode,
    Fact,
    NodeStatus,
    NodeType,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.reflection.pair_scoring import (
    SCORE_BLOCK,
    similar_pairs,
    stack_uniform_width,
)
from epimemer.pipelines.reflection.review import SIMILARITY_NOMINATION_THRESHOLD
from epimemer.pipelines.reflection.similarity_decisions import already_judged_pairs
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
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    model_id: str | None = None,
    statuses: frozenset[NodeStatus] = frozenset({NodeStatus.ACTIVE}),
) -> list[tuple[Fact, Fact, float]]:
    """Find pairs of facts with high embedding similarity.

    High semantic similarity between facts can indicate either:
    - Redundancy (same fact stated differently)
    - Contradiction (opposing claims about the same thing)
    - Recurrence, when one side is HISTORICAL: the same claim true again

    Returns candidate pairs for further analysis. Excludes pairs anybody has
    already judged — see `already_linked` below.

    `statuses` widens what is compared, and the default keeps this pass to the
    active set as before. Reflect asks twice rather than once: an active pair
    and a historical/active pair mean different things, and a caller that mixed
    them would file a claim beside its own successor under the word
    *contradiction* — which is precisely the misreading the `recurs` verdict
    exists to prevent.

    Args:
        storage: The storage backend.
        embedding_provider: Used to determine the model_id for embeddings.
        similarity_threshold: Minimum cosine similarity for a pair to be included.
        model_id: Override the model_id used for embedding lookup.
        statuses: Which nodes are compared. ACTIVE alone by default.

    Returns:
        A list of (fact_a, fact_b, similarity_score) sorted by score descending.
    """
    effective_model_id = model_id or embedding_provider.model_id

    candidates: list[EpistemicNode] = []
    for status in sorted(statuses, key=lambda s: s.value):
        candidates.extend(await storage.query_nodes(node_type=NodeType.FACT, status=status))
    facts: list[Fact] = [f for f in candidates if isinstance(f, Fact)]

    if len(facts) < 2:
        return []

    # One fetch for every fact's vector, on the same terms as the edge reads
    # below: this walks the whole active fact set, so asking per fact made the
    # phase round-trip bound on a networked backend.
    by_item = await storage.get_embeddings_for_items(
        [fact.id for fact in facts], model_id=effective_model_id
    )
    fact_vectors: dict[str, list[float]] = {
        fact.id: by_item[fact.id][0].vector for fact in facts if by_item[fact.id]
    }

    # Every pair somebody has already judged, whichever way the judgment went.
    # `ASSESSED` is what makes the set complete: before it there was no writer
    # for the *compatible* verdict, so a declined pair was recorded nowhere and
    # came back on every pass — thirteen of eighteen, on the graph that
    # motivated the verdict record. The other three are the pairs that got an action.
    already_linked = await already_judged_pairs([fact.id for fact in facts], storage)

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
