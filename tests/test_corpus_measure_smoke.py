"""The corpus measuring instrument still measures what it claims to.

`scripts/corpus_measure.py` produced the numbers behind ISSUES.md #59 and #60,
and it reads two thresholds *out of the reflection code* rather than restating
them. That is the right design and it is exactly what rots silently: rename the
keyword and the script keeps running, reporting a survival rate for a threshold
nothing uses.

The graph-reading half needs a populated SurrealDB and is not covered here —
what is covered is everything that can be wrong without anyone noticing.
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "corpus_measure.py"


def _module():
    """Load the script as a module — it is not on the import path."""
    sys.path.insert(0, str(_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("corpus_measure", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def measure():
    return _module()


class TestTheThresholdsComeFromTheCode:
    """The whole point of `_threshold`: one number, one home."""

    def test_both_thresholds_resolve_from_the_reflection_functions(self, measure):
        thresholds = measure._thresholds()

        assert set(thresholds) == {"fact", "topic"}
        assert all(0.0 < value < 1.0 for value in thresholds.values())

    def test_the_fact_threshold_is_what_contradiction_detection_defaults_to(
        self, measure
    ):
        """Reading it from the signature is what keeps the instrument honest.

        If `similarity_threshold` is renamed or stops being a parameter, this
        raises rather than letting the script fall back to a stale literal.
        """
        from epimemer.pipelines.reflection.contradiction_detection import (
            detect_contradictions,
        )

        assert measure._threshold(detect_contradictions) == (
            measure._thresholds()["fact"]
        )

    def test_a_renamed_parameter_fails_loudly(self, measure):
        def scorer(vectors, cutoff: float = 0.5):
            return []

        with pytest.raises(KeyError):
            measure._threshold(scorer)


class TestTheDistributionReportsWhatItPromises:
    def test_over_window_counts_only_what_exceeds_it(self, measure):
        result = measure._distribution([10, 256, 257, 500], window=256)

        assert result["count"] == 4
        assert result["over_window"] == 2, "256 exactly is not truncated"
        assert result["over_window_pct"] == 50.0

    def test_worst_lost_pct_is_the_share_of_the_longest_text_cut(self, measure):
        result = measure._distribution([512], window=256)

        assert result["worst_lost_pct"] == 50.0

    def test_nothing_over_the_window_loses_nothing(self, measure):
        result = measure._distribution([10, 20, 30], window=256)

        assert result["over_window"] == 0
        assert result["worst_lost_pct"] == 0.0

    def test_an_empty_corpus_reports_a_count_rather_than_raising(self, measure):
        assert measure._distribution([], window=256) == {"count": 0}


class TestSurvivalIsMeasuredOnRealPairs:
    def test_identical_vectors_all_survive(self, measure):
        vectors = np.ones((5, 4))

        result = measure._survival(vectors, 0.80)

        assert result["pairs"] == 10
        assert result["survivors"] == 10
        assert result["survival_rate_pct"] == 100.0

    def test_orthogonal_vectors_never_survive(self, measure):
        result = measure._survival(np.eye(4), 0.80)

        assert result["pairs"] == 6
        assert result["survivors"] == 0
        assert result["projected"]["10000"]["pairs"] == 0

    def test_one_item_has_no_pairs_and_no_projection(self, measure):
        assert measure._survival(np.ones((1, 4)), 0.80) == {"items": 1, "pairs": 0}


class TestTheScoreSpreadIsWhatSeparatesNoiseFromDistance:
    """A rate of 0 says nothing about *how far* below the threshold a corpus is,
    and that is the difference between #60 firing and not."""

    def test_orthogonal_vectors_sit_at_zero_throughout(self, measure):
        spread = measure._score_spread(np.eye(8))

        assert spread["score_p50"] == 0.0
        assert spread["score_max"] == 0.0

    def test_identical_vectors_sit_at_one_throughout(self, measure):
        spread = measure._score_spread(np.ones((6, 4)))

        assert spread["score_p50"] == pytest.approx(1.0)
        assert spread["score_max"] == pytest.approx(1.0)

    def test_a_single_vector_yields_no_spread(self, measure):
        assert measure._score_spread(np.ones((1, 4))) == {}


class TestScalingStepsStayInsideTheCorpus:
    def test_steps_never_exceed_the_items_available(self, measure):
        steps = measure._scaling(np.eye(120), 0.80)

        assert [step["items"] for step in steps] == [50, 100]
        assert all(step["survivors"] == 0 for step in steps)

    def test_a_corpus_smaller_than_the_first_step_yields_no_steps(self, measure):
        assert measure._scaling(np.eye(10), 0.80) == []
