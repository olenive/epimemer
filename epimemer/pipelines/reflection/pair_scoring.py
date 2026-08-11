"""Batched pairwise cosine scoring, shared by the phases that need it.

Two `reflect` phases ask the same question of different node types — which
pairs in this set are similar enough to be worth acting on — and both are
quadratic in the set. Neither has redundancy left to remove: the comparisons
are genuine work, so the answer is to do that work in bulk rather than less of
it, one matrix product per block instead of one Python call per pair.

This lives on its own because it was written twice: once for facts (#39) and
once, by copying, for topics (#47). The second time is what makes it shared —
the zero-vector rule below is subtle enough that two copies would eventually
disagree, and a disagreement here is silent.

The exponent is unchanged by any of this. A constant factor moves a quadratic
crossing by roughly its square root, which is why the guards against regression
are shape tests plus `make bench` rather than wall-clock assertions.
"""

import numpy as np

# Rows scored per matrix product. Peak allocation is block × items, so this
# bounds the working set: the whole matrix at 10,000 items would be 800 MB of
# float64, which trades a timeout for an allocation failure.
SCORE_BLOCK = 512


def _unit_rows(vectors: np.ndarray) -> np.ndarray:
    """Row-normalized copy, with zero rows left as zeros rather than NaN.

    A zero vector has no direction, so the loops this replaced special-cased it
    to 0.0 similarity. Dividing by its norm here would put NaN through an entire
    row of the product, and NaN compares false against the threshold — so every
    comparison involving *any* other item in that block would silently stop
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

    Returned in index order, which is the order the loops this replaced produced
    them in. Callers sort by score with Python's stable sort, so preserving it is
    what keeps tied pairs in the same sequence as before — and with a mock
    embedding provider, or any corpus of near-duplicates, almost every pair ties.

    The remaining Python-level loop runs over *surviving* pairs, not all of them:
    the threshold has already been applied by the comparison inside the block.
    That residue is inherent — a caller has to look at each survivor anyway — and
    it is a tuple construction rather than a 384-component dot product.

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
        # is the item's index in the full set, and the diagonal lands where the
        # two are equal.
        upper = cols > rows + start
        for row, col in zip(rows[upper], cols[upper]):
            found.append((start + int(row), int(col), float(scores[row, col])))
    return found


def stack_uniform_width(
    keys: list[str], vector_by_key: dict[str, list[float]]
) -> tuple[list[str], np.ndarray]:
    """The keys that share the majority vector width, and their matrix.

    A ragged set cannot form a matrix, so odd-width vectors are dropped rather
    than compared — exactly as items with no embedding at all are. The loops
    this replaced "handled" the case by zipping two vectors together, which
    silently truncated to the shorter one and scored the pair on a prefix.

    Width is taken from the first key present rather than by majority vote:
    a mixed-width graph is a migration in progress, and there is no honest
    answer about which half is right. Returns an empty matrix for fewer than
    two survivors, which every caller reads as "nothing to compare".
    """
    present = [key for key in keys if key in vector_by_key]
    if len(present) < 2:
        return [], np.zeros((0, 0))

    width = len(vector_by_key[present[0]])
    kept = [key for key in present if len(vector_by_key[key]) == width]
    if len(kept) < 2:
        return [], np.zeros((0, 0))

    return kept, np.array([vector_by_key[key] for key in kept], dtype=np.float64)
