"""A bounded ring of the most recent records, with nothing else in it.

Two features want the same structure and neither should own it: the event log
keeps the coarse graph actions (`EVENT_LOG.md` §4), and retrieval provenance
keeps the records of what each tool returned (`RETRIEVAL_PROVENANCE.md` §3.2).
One implementation, two instances, sized differently — a second ring written out
by hand is a second eviction rule, and the two drift the first time one is
tuned.

**Values, not a buffer.** `remember` returns a new tuple rather than mutating in
place, so a ring handed to a reader cannot change underneath it — and the hub
keeps these in a dict it also iterates while fanning out. The cost is a copy of
`capacity` references per append, which at these capacities is nothing next to
serializing the record that prompted it.
"""

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

# How many coarse acts a session keeps. Traced to the measured numbers rather
# than to the withdrawn estimates (§3, corrected): a 25-node
# `store_decomposition` emits 176 fine-grained events and **one** act, because
# it is one transaction. So the coarse stream is two orders of magnitude
# smaller than the one that prompted "a raw log is a firehose", and a few
# hundred entries is a long working session rather than a few seconds of one.
# Not configurable, deliberately — a knob here is one more thing that can be set
# to a number nobody measured (§10, resolved by construction).
LOG_RING_CAPACITY = 512

# Retrieval records carry response payloads and id lists, so they are bounded by
# *bytes* rather than by count; ~20 is what §3.2 asks for, and the per-record
# caps live with that feature rather than here.
RETRIEVAL_RING_CAPACITY = 20


def remember[T](ring: Sequence[T], item: T, *, capacity: int) -> tuple[T, ...]:
    """`ring` with `item` appended, oldest dropped once it is full."""
    if capacity < 0:
        # `[-capacity:]` would read a negative bound as "all but the last n",
        # which inverts the eviction rule instead of failing.
        raise ValueError(f"ring capacity cannot be negative: {capacity}")
    if capacity == 0:
        return ()
    return (*ring, item)[-capacity:]


def backfill[T](ring: Sequence[T]) -> list[T]:
    """What a subscriber replays — oldest first, the order it happened in."""
    return list(ring)
