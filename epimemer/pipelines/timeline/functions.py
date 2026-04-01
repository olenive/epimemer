"""Pure functional interface for Timeline operations.

All functions take a Timeline and return a new Timeline (immutable-style).
Backed by a sorted list with bisect for efficient lookups.
The implementation is swappable later without changing the interface.
"""

from bisect import bisect_left, insort_left
from datetime import datetime

from epimemer.core.types import Timeline, Timepoint


def add_timepoint(
    timeline: Timeline,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    label: str | None = None,
    metadata: dict | None = None,
) -> tuple[Timeline, Timepoint]:
    """Add a timepoint to a timeline, maintaining sorted order.

    Timepoints with concrete `start` are sorted by start datetime.
    Vague timepoints (no start) are appended after all concrete ones.

    Returns the updated timeline and the new timepoint.
    """
    tp = Timepoint(
        start=start,
        end=end,
        label=label,
        metadata=metadata or {},
    )

    concrete, vague = _split_concrete_vague(timeline.timepoints)

    if start is not None:
        # Insert in sorted position among concrete timepoints
        insort_left(concrete, tp, key=lambda t: t.start)
    else:
        vague.append(tp)

    new_timepoints = concrete + vague
    new_timeline = timeline.model_copy(update={"timepoints": new_timepoints})
    return new_timeline, tp


def remove_timepoint(
    timeline: Timeline,
    timepoint_id: str,
) -> Timeline:
    """Remove a timepoint by ID. Returns unchanged timeline if ID not found."""
    new_timepoints = [tp for tp in timeline.timepoints if tp.id != timepoint_id]
    return timeline.model_copy(update={"timepoints": new_timepoints})


def get_timepoint(
    timeline: Timeline,
    timepoint_id: str,
) -> Timepoint | None:
    """Get a timepoint by ID, or None if not found."""
    for tp in timeline.timepoints:
        if tp.id == timepoint_id:
            return tp
    return None


def find_nearest(
    timeline: Timeline,
    target: datetime,
    k: int = 5,
) -> list[Timepoint]:
    """Find the k timepoints nearest to a target datetime.

    Only considers timepoints with concrete start datetimes.
    Results are sorted by distance from target.
    """
    concrete = [tp for tp in timeline.timepoints if tp.start is not None]
    if not concrete:
        return []

    # Use bisect to find insertion point
    starts = [tp.start for tp in concrete]
    idx = bisect_left(starts, target)

    # Gather candidates around the insertion point
    candidates: list[tuple[float, Timepoint]] = []
    for i in range(max(0, idx - k), min(len(concrete), idx + k)):
        dist = abs((concrete[i].start - target).total_seconds())
        candidates.append((dist, concrete[i]))

    candidates.sort(key=lambda x: x[0])
    return [tp for _, tp in candidates[:k]]


def get_in_range(
    timeline: Timeline,
    start: datetime,
    end: datetime,
) -> list[Timepoint]:
    """Get all timepoints that overlap with a time range.

    A timepoint overlaps if:
    - Its start falls within [start, end], or
    - Its interval [tp.start, tp.end] overlaps with [start, end], or
    - It has no concrete dates (excluded from range queries)
    """
    results: list[Timepoint] = []

    concrete = [tp for tp in timeline.timepoints if tp.start is not None]

    # Use bisect for efficient range finding
    starts = [tp.start for tp in concrete]
    left = bisect_left(starts, start)

    for i in range(left, len(concrete)):
        tp = concrete[i]
        if tp.start > end:
            break

        # Point timepoint: start is within range
        if tp.end is None:
            results.append(tp)
        # Interval timepoint: check overlap
        elif tp.start <= end and tp.end >= start:
            results.append(tp)

    # Also check intervals that started before the range but extend into it
    for i in range(0, left):
        tp = concrete[i]
        if tp.end is not None and tp.end >= start:
            results.append(tp)

    return results


def reorder_timepoints(timeline: Timeline) -> Timeline:
    """Re-sort timepoints: concrete by start datetime, vague at the end."""
    concrete, vague = _split_concrete_vague(timeline.timepoints)
    concrete.sort(key=lambda tp: tp.start)
    return timeline.model_copy(update={"timepoints": concrete + vague})


def _split_concrete_vague(
    timepoints: list[Timepoint],
) -> tuple[list[Timepoint], list[Timepoint]]:
    """Split timepoints into concrete (has start) and vague (no start)."""
    concrete = [tp for tp in timepoints if tp.start is not None]
    vague = [tp for tp in timepoints if tp.start is None]
    return concrete, vague
