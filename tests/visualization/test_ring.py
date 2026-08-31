"""The bounded ring both the log and the retrieval records hang off.

Pure: no hub, no socket, no session. One implementation with two instances is
the point (`EVENT_LOG.md` §4.3) — a second ring written out by hand is a second
eviction rule, and the two would differ the first time one of them was tuned.
"""

import pytest

from epimemer.visualization.ring import (
    LOG_RING_CAPACITY,
    RETRIEVAL_RING_CAPACITY,
    backfill,
    remember,
)


def _filled(n: int, *, capacity: int) -> tuple[int, ...]:
    ring: tuple[int, ...] = ()
    for i in range(n):
        ring = remember(ring, i, capacity=capacity)
    return ring


class TestBounds:
    def test_a_ring_below_its_capacity_keeps_everything(self):
        assert _filled(3, capacity=5) == (0, 1, 2)

    def test_the_oldest_is_evicted_first(self):
        assert _filled(7, capacity=3) == (4, 5, 6)

    def test_capacity_is_never_exceeded(self):
        assert len(_filled(1000, capacity=4)) == 4

    def test_a_capacity_of_zero_remembers_nothing(self):
        """Not a special case in the code, and it must not become one: a ring
        sized to nothing is empty, not unbounded."""
        assert _filled(5, capacity=0) == ()

    def test_a_negative_capacity_is_refused(self):
        """`(*ring, item)[-capacity:]` reads a negative bound as "all but the
        last n", which silently inverts the eviction rule."""
        with pytest.raises(ValueError):
            remember((), "x", capacity=-1)


class TestPurity:
    def test_remember_leaves_its_input_alone(self):
        """The hub keeps a ring per session in a dict it also iterates. A ring
        that mutated in place would have every reader sharing one buffer."""
        before = (1, 2, 3)

        after = remember(before, 4, capacity=3)

        assert before == (1, 2, 3)
        assert after == (2, 3, 4)


class TestBackfill:
    def test_backfill_replays_oldest_first(self):
        """A browser has to see the acts in the order they happened, not the
        order the ring happens to store them in."""
        assert backfill(_filled(5, capacity=3)) == [2, 3, 4]

    def test_an_empty_ring_backfills_nothing(self):
        assert backfill(()) == []


class TestCapacities:
    """§3, corrected: the ring bound must trace to the *measured* numbers."""

    def test_the_log_ring_holds_far_more_acts_than_a_task_produces(self):
        # 176 events per 25-node ingest collapse to one act, so a session's
        # worth of readable history is hundreds of entries, not hundreds of
        # thousands.
        assert LOG_RING_CAPACITY >= 100

    def test_the_retrieval_ring_is_small_because_its_records_are_not(self):
        assert RETRIEVAL_RING_CAPACITY < LOG_RING_CAPACITY
