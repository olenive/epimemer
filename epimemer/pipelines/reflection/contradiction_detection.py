"""Contradiction detection for the reflection layer.

Finds pairs of active facts with high embedding similarity that may
represent contradictions or redundancies, for further analysis.

This phase is the one that fails first as a graph grows (#39). Pairs are
quadratic in facts and there is no redundancy left to remove — the comparisons
are genuine work — so the fix was to do that work in bulk rather than less of
it: one matrix product per block instead of one Python call per pair. The
exponent is unchanged, which is why the guard against regression is a shape
test plus `make bench`, not a wall-clock assertion.
"""

import numpy as np

from epimemer.core.types import (
    EdgeType,
    Fact,
    NodeType,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend

# Rows scored per matrix product. Peak allocation is block × facts, so this
# bounds the working set: the whole matrix at 10,000 facts would be 800 MB of
# float64, which trades a timeout for an allocation failure.
SCORE_BLOCK = 512


def _unit_rows(vectors: np.ndarray) -> np.ndarray:
    """Row-normalized copy, with zero rows left as zeros rather than NaN.

    A zero vector has no direction, so the old loop special-cased it to 0.0
    similarity. Dividing by its norm here would put NaN through an entire row of
    the product, and NaN compares false against the threshold — so every
    comparison involving *any* other fact in that block would silently stop
    firing. Loud in the arithmetic, invisible in the answer.
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0.0, 1.0, norms)


def similar_pairs(
    vectors: np.ndarray,
    threshold: float,
    *,
    block: int = SCORE_BLOCK,
) -> list[tuple[int, int, float]]:
    """Every ``(i, j, score)`` with ``i < j`` scoring at or above `threshold`.

    Returned in index order, which is the order the loop this replaced produced
    them in. The caller sorts by score with Python's stable sort, so preserving
    it is what keeps tied pairs in the same sequence as before.

    The remaining Python-level loop runs over *surviving* pairs, not all of
    them: the threshold has already been applied by the comparison inside the
    block. That residue is inherent — the caller has to check each survivor's
    metacontext frame one at a time anyway — and it is a tuple construction
    rather than a 384-component dot product.

    `block` is exposed for tests, which need the seams exercised at sizes small
    enough to reason about.
    """
    count = len(vectors)
    if count < 2:
        return []

    unit = _unit_rows(vectors)
    found: list[tuple[int, int, float]] = []
    for start in range(0, count, block):
        scores = unit[start : start + block] @ unit.T
        rows, cols = np.nonzero(scores >= threshold)
        # Keep the strict upper triangle. `rows` is block-local; `start + row`
        # is the fact's index in the full set, and the diagonal lands where the
        # two are equal.
        upper = cols > rows + start
        for row, col in zip(rows[upper], cols[upper]):
            found.append((start + int(row), int(col), float(scores[row, col])))
    return found


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

    # Score every pair at once. Facts whose stored vector is a different width
    # are dropped rather than compared, exactly as facts with no embedding at
    # all are: a ragged set cannot form a matrix, and the loop this replaced
    # "handled" the case by zipping the vectors together, which silently
    # truncated to the shorter one and scored the pair on a prefix.
    fact_list = [f for f in facts if f.id in fact_vectors]
    if len(fact_list) < 2:
        return []

    width = len(fact_vectors[fact_list[0].id])
    fact_list = [f for f in fact_list if len(fact_vectors[f.id]) == width]
    if len(fact_list) < 2:
        return []

    vectors = np.array([fact_vectors[f.id] for f in fact_list], dtype=np.float64)

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
