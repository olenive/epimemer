"""Rank fusion, tested as a pure function.

Cosine similarity and BM25 are on incomparable scales with no calibration
between them, so nothing here ever sees a score — only positions. That is the
whole argument for Reciprocal Rank Fusion over a weighted sum: there is no magic
number to tune, and therefore no magic number to re-tune forever.
"""

import pytest

from epimemer.pipelines.query.fusion import fuse, rrf_scores


class TestRRFScores:
    def test_score_is_the_sum_of_reciprocal_ranks(self):
        scores = rrf_scores([["a", "b"], ["b"]], k=60)

        assert scores["a"] == pytest.approx(1 / 61)
        assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)

    def test_rrf_promotes_a_rank_one_lexical_hit(self):
        """A rank-1 hit in any single list scores `1/61`, the most one list can
        contribute — so an exact identifier match arrives near the top even
        though the other arm never saw it."""
        vector = [f"v{i}" for i in range(10)]
        lexical = ["exact"]

        order = fuse([vector, lexical], limit=10)

        assert order[0] == "exact"

    def test_an_id_in_two_lists_beats_a_rank_one_id_in_one(self):
        """The correction in §3.1, pinned rather than left as a footnote.

        "A rank-1 lexical hit always lands in the top few" is false under list
        overlap: `1/70 + 1/71` beats `1/61`. This is not a defect — agreement
        between two independent arms *should* outweigh one arm's best guess —
        but it is why R2 exists, because the motivating scenario is exactly
        where overlap peaks.
        """
        vector = [f"n{i}" for i in range(10)]
        lexical = [*[f"n{i}" for i in range(10)], "exact"]

        scores = rrf_scores([vector, lexical])

        assert scores["n9"] > scores["exact"]

    def test_an_empty_ranking_contributes_nothing(self):
        assert rrf_scores([[], ["a"]]) == {"a": pytest.approx(1 / 61)}

    def test_no_rankings_score_nothing(self):
        assert rrf_scores([]) == {}


class TestFuse:
    def test_fuse_orders_by_score_and_truncates(self):
        order = fuse([["a", "b", "c"], ["b", "c"]], limit=2)

        # `a` leads one list and is absent from the other; `b` and `c` are in
        # both, and two mid-rank appearances outweigh one first place.
        assert order == ["b", "c"]

    def test_ties_break_deterministically(self):
        """Two runs of the same search must not return two different orders.

        RRF ties are common — every id alone at the same rank in its own list
        scores identically — and the engines' own tie order is arbitrary.
        """
        first = fuse([["b"], ["a"]], limit=10)
        second = fuse([["a"], ["b"]], limit=10)

        assert first == second

    def test_declared_term_top_hit_survives_overlapping_lists(self):
        """R2, and the reason it is a rule rather than an emergent property.

        The identifier is lexical rank 1. The vector list is ten nodes that all
        also appear in the lexical list — the graph where every ticket id embeds
        alike, which is the case this feature exists for — so each of them
        outscores the identifier and pure RRF pushes it to rank 11, off a top-10
        cut. Removing `protected` here fails this test; that is the point of it.
        """
        vector = [f"n{i}" for i in range(10)]
        lexical = ["exact", *[f"n{i}" for i in range(10)]]

        unprotected = fuse([vector, lexical], limit=10)
        protected = fuse([vector, lexical], limit=10, protected=["exact"])

        assert "exact" not in unprotected
        assert "exact" in protected

    def test_a_protected_id_keeps_its_fused_position(self):
        """Protection is about presence, not promotion.

        A rescued id sits where its fused score puts it, not at the front.
        Ranking it above better-supported results would be the weighted-sum
        problem again, arriving by a different door.
        """
        vector = [f"n{i}" for i in range(10)]
        lexical = ["exact", *[f"n{i}" for i in range(10)]]

        order = fuse([vector, lexical], limit=10, protected=["exact"])

        assert order[-1] == "exact"

    def test_a_protected_id_already_returned_is_not_duplicated(self):
        order = fuse([["a", "b"]], limit=10, protected=["a"])

        assert order == ["a", "b"]

    def test_protection_can_exceed_the_limit(self):
        """The documented departure from pure RRF: a declared term's hit
        survives *past* the top-k cut rather than displacing something."""
        order = fuse([["a", "b", "c"], ["a", "b", "c"]], limit=2, protected=["c"])

        assert order == ["a", "b", "c"]

    def test_a_protected_id_absent_from_every_ranking_is_not_invented(self):
        """Protection rescues a hit that was scored and cut, not an id nobody
        found."""
        order = fuse([["a"]], limit=10, protected=["never-seen"])

        assert order == ["a"]
