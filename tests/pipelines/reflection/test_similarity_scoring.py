"""`similar_pairs` — the batched replacement for the pairwise loop (#39, #47).

Vectorizing changes *how* the score is computed, not *which* pairs come back, so
almost everything here is checked against the implementation being replaced
rather than against hand-written expectations. That naive version is kept in
this file as the oracle: it is four lines, it is obviously correct, and it is
the thing the fast path has to agree with.

The two places a batched implementation can go wrong that a loop cannot are
blocking (a pair straddling a block boundary, or the diagonal being kept) and
normalization (a zero vector turning the whole row into NaN), so both get their
own tests.

It now serves two phases (facts and topics), which raises the stakes on the
odd-width rule `stack_uniform_width` enforces: a set that cannot form a matrix
has to shrink to one that can, rather than raise or score a pair on a prefix.
"""

import math

import numpy as np

from epimemer.pipelines.reflection.pair_scoring import (
    similar_pairs,
    stack_uniform_width,
)


def _naive(vectors, threshold: float) -> list[tuple[int, int, float]]:
    """The pairwise loop #39 removed, kept as the oracle."""

    def cosine(a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    found = []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            score = cosine(vectors[i], vectors[j])
            if score >= threshold:
                found.append((i, j, score))
    return found


def _same(got, expected) -> None:
    """Same pairs in the same order, scores equal to floating-point slack.

    The scores are not bit-identical by construction: the loop divides one dot
    product by two norms, the matrix form normalizes the rows first. That is a
    different order of operations on the same arithmetic.
    """
    assert [(i, j) for i, j, _ in got] == [(i, j) for i, j, _ in expected]
    for (_, _, a), (_, _, b) in zip(got, expected):
        assert a == b or abs(a - b) < 1e-9


def _random_vectors(count: int, width: int, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=(count, width))


class TestItAgreesWithTheLoopItReplaced:

    def test_random_vectors_at_a_middling_threshold(self):
        vectors = _random_vectors(40, 16)
        _same(similar_pairs(vectors, 0.1), _naive(vectors.tolist(), 0.1))

    def test_a_threshold_nothing_clears(self):
        vectors = _random_vectors(20, 16)
        assert similar_pairs(vectors, 0.99) == []

    def test_a_threshold_everything_clears(self):
        """The dense case: every pair survives, which is the shape the
        synthetic benchmark corpus produces and the worst case for memory."""
        vectors = _random_vectors(20, 8)
        assert len(similar_pairs(vectors, -1.0)) == 20 * 19 // 2

    def test_fewer_than_two_vectors_is_not_a_pair(self):
        assert similar_pairs(_random_vectors(1, 4), 0.0) == []
        assert similar_pairs(np.zeros((0, 4)), 0.0) == []


class TestBlocking:
    """Blocking bounds peak memory at block × count rather than count².

    At 10,000 facts a full float64 similarity matrix is 800 MB, so the naive
    vectorization trades a timeout for an allocation failure. These tests fix
    the block size well below the input so the seams are actually exercised.
    """

    def test_every_block_size_gives_the_same_answer(self):
        vectors = _random_vectors(37, 8)
        expected = _naive(vectors.tolist(), 0.05)
        for block in (1, 2, 5, 36, 37, 38, 1000):
            _same(similar_pairs(vectors, 0.05, block=block), expected)

    def test_a_pair_straddling_a_block_boundary_survives(self):
        """Identical vectors either side of the seam: the pair spans two blocks
        and belongs to neither on its own."""
        vectors = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]])
        assert similar_pairs(vectors, 0.99, block=2) == [(0, 2, 1.0)]

    def test_the_diagonal_is_never_a_pair(self):
        """A vector is perfectly similar to itself, and every block contains a
        piece of the diagonal. Only i < j counts."""
        vectors = _random_vectors(9, 4)
        for block in (1, 3, 4, 9):
            pairs = similar_pairs(vectors, -1.0, block=block)
            assert all(i < j for i, j, _ in pairs)


class TestDegenerateVectors:

    def test_a_zero_vector_pairs_with_nothing(self):
        """Normalizing divides by the norm, and a zero row would take the whole
        matrix to NaN — at which case every comparison silently goes false and
        contradiction detection stops working with no error."""
        vectors = np.array([[0.0, 0.0], [1.0, 1.0], [1.0, 1.0]])
        pairs = similar_pairs(vectors, -1.0)

        assert not np.isnan([score for _, _, score in pairs]).any()
        assert [(i, j) for i, j, _ in pairs] == [(0, 1), (0, 2), (1, 2)]
        assert dict(((i, j), s) for i, j, s in pairs)[(0, 1)] == 0.0

    def test_all_zero_vectors_score_zero_rather_than_failing(self):
        pairs = similar_pairs(np.zeros((4, 3)), -1.0)
        assert len(pairs) == 6
        assert all(score == 0.0 for _, _, score in pairs)


class TestOrdering:

    def test_pairs_come_back_in_index_order(self):
        """The caller sorts by score, and Python's sort is stable — so ties keep
        this order, and it has to match the loop's to leave the answer alone."""
        vectors = _random_vectors(25, 8)
        pairs = similar_pairs(vectors, -1.0, block=4)
        assert [(i, j) for i, j, _ in pairs] == sorted((i, j) for i, j, _ in pairs)

    def test_identical_vectors_keep_their_index_order(self):
        """Every score ties at 1.0, so ordering is entirely the traversal's."""
        vectors = np.ones((5, 3))
        pairs = similar_pairs(vectors, 0.5, block=2)
        assert [(i, j) for i, j, _ in pairs] == [
            (i, j) for i in range(5) for j in range(i + 1, 5)
        ]


class TestStackUniformWidth:
    """The ragged-set rule, now that two phases depend on it (#47).

    A mixed-width vector set is a re-embedding in progress. It cannot form a
    matrix, so something has to give: the loops this replaced gave silently,
    zipping two vectors together and scoring the pair on the shorter one's
    length. Dropping the odd ones out is the same treatment items with no
    embedding at all already get.
    """

    def test_it_keeps_everything_when_widths_agree(self):
        vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [1.0, 1.0]}

        kept, matrix = stack_uniform_width(["a", "b", "c"], vectors)

        assert kept == ["a", "b", "c"]
        assert matrix.shape == (3, 2)

    def test_it_drops_the_odd_width_out(self):
        vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0], "wrong": [1.0, 0.0, 0.0]}

        kept, matrix = stack_uniform_width(["a", "b", "wrong"], vectors)

        assert kept == ["a", "b"]
        assert matrix.shape == (2, 2)

    def test_width_comes_from_the_first_key_present(self):
        """Not a majority vote: there is no honest answer about which half wins.

        First-present is at least stable and explicable — the caller's order
        decides — where a majority silently flips the survivors as a migration
        crosses the halfway mark.
        """
        vectors = {"a": [1.0, 0.0], "b": [1.0, 0.0, 0.0], "c": [0.0, 1.0, 0.0]}

        assert stack_uniform_width(["a", "b", "c"], vectors)[0] == []
        assert stack_uniform_width(["b", "c", "a"], vectors)[0] == ["b", "c"]

    def test_keys_without_a_vector_are_skipped(self):
        vectors = {"a": [1.0, 0.0], "c": [0.0, 1.0]}

        kept, matrix = stack_uniform_width(["a", "unembedded", "c"], vectors)

        assert kept == ["a", "c"]
        assert matrix.shape == (2, 2)

    def test_fewer_than_two_survivors_is_nothing_to_compare(self):
        vectors = {"a": [1.0, 0.0]}

        assert stack_uniform_width(["a"], vectors)[0] == []
        assert stack_uniform_width([], vectors)[0] == []
        assert stack_uniform_width(["a", "missing"], vectors)[0] == []

    def test_the_matrix_rows_match_the_keys_it_returns(self):
        """The caller indexes back into its own list by position, so a row that
        does not line up with its key attributes a score to the wrong node."""
        vectors = {"a": [1.0, 0.0], "skip": [9.0], "b": [0.0, 1.0]}

        kept, matrix = stack_uniform_width(["a", "skip", "b"], vectors)

        assert kept == ["a", "b"]
        assert matrix.tolist() == [[1.0, 0.0], [0.0, 1.0]]
