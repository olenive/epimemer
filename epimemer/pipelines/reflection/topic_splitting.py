"""Topic splitting for the reflection layer.

Identifies topics whose associated material spans multiple distinct
sub-themes (high internal variance) and splits them into subtopics.
The original topic becomes the parent.

Split decision:
    1. Gather material (facts, inferences) linked to the topic
    2. Embed all material items
    3. Bisect: partition into two clusters (k-means with k=2)
    4. Compare inter-cluster distance to intra-cluster spread
    5. If ratio > threshold, the split is meaningful → propose it

Proposals only. The clusters say *that* a topic covers two things; naming the
subtopics is a judgement call, so the calling agent supplies the descriptions
and applies the split through `apply_reflection`.
"""

import math

# --- Vector math helpers ---


def _centroid(vectors: list[list[float]]) -> list[float]:
    """Compute the centroid of a list of vectors."""
    n = len(vectors)
    dim = len(vectors[0])
    return [sum(v[d] for v in vectors) / n for d in range(dim)]


def _euclidean_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _mean_distance_to_centroid(vectors: list[list[float]], centroid: list[float]) -> float:
    """Average Euclidean distance from each vector to the centroid."""
    if not vectors:
        return 0.0
    return sum(_euclidean_distance(v, centroid) for v in vectors) / len(vectors)


def _bisect(vectors: list[list[float]], max_iterations: int = 20) -> tuple[list[int], list[int]]:
    """Partition vectors into two clusters using k-means with k=2.

    Returns two lists of indices. Uses the two most distant vectors
    as initial centroids for stability.
    """
    n = len(vectors)
    if n < 2:
        return list(range(n)), []

    # Initialize: pick the two most distant vectors as seeds
    max_dist = -1.0
    seed_a, seed_b = 0, 1
    for i in range(n):
        for j in range(i + 1, n):
            d = _euclidean_distance(vectors[i], vectors[j])
            if d > max_dist:
                max_dist = d
                seed_a, seed_b = i, j

    centroid_a = list(vectors[seed_a])
    centroid_b = list(vectors[seed_b])

    for _ in range(max_iterations):
        cluster_a: list[int] = []
        cluster_b: list[int] = []
        for i in range(n):
            if _euclidean_distance(vectors[i], centroid_a) <= _euclidean_distance(
                vectors[i], centroid_b
            ):
                cluster_a.append(i)
            else:
                cluster_b.append(i)

        # Handle empty clusters
        if not cluster_a or not cluster_b:
            return list(range(n)), []

        new_centroid_a = _centroid([vectors[i] for i in cluster_a])
        new_centroid_b = _centroid([vectors[i] for i in cluster_b])

        # Convergence check
        if new_centroid_a == centroid_a and new_centroid_b == centroid_b:
            break
        centroid_a = new_centroid_a
        centroid_b = new_centroid_b

    return cluster_a, cluster_b


def should_split(
    vectors: list[list[float]],
    *,
    split_ratio: float = 1.5,
    min_items: int = 4,
) -> bool:
    """Determine if a set of material vectors warrants splitting.

    Bisects into two clusters, then checks whether the inter-cluster
    distance is sufficiently larger than the average intra-cluster spread.

    Args:
        vectors: Embedding vectors of the associated material.
        split_ratio: Minimum ratio of inter-cluster distance to mean
            intra-cluster spread. Higher = more conservative.
        min_items: Minimum number of material items before considering a split.

    Returns:
        True if a split is warranted.
    """
    if len(vectors) < min_items:
        return False

    cluster_a_idx, cluster_b_idx = _bisect(vectors)
    if not cluster_a_idx or not cluster_b_idx:
        return False

    vecs_a = [vectors[i] for i in cluster_a_idx]
    vecs_b = [vectors[i] for i in cluster_b_idx]

    centroid_a = _centroid(vecs_a)
    centroid_b = _centroid(vecs_b)

    inter_distance = _euclidean_distance(centroid_a, centroid_b)
    intra_a = _mean_distance_to_centroid(vecs_a, centroid_a)
    intra_b = _mean_distance_to_centroid(vecs_b, centroid_b)
    mean_intra = (intra_a + intra_b) / 2.0

    if mean_intra == 0.0:
        # All vectors in each cluster are identical — no internal spread
        # but clusters differ, so split is warranted if they're apart
        return inter_distance > 0.0

    return inter_distance / mean_intra >= split_ratio
