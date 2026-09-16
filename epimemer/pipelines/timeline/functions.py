"""Pure functional interface for Timeline operations.

All functions take a Timeline and return a new Timeline (immutable-style).
Backed by a sorted list with bisect for efficient lookups.
The implementation is swappable later without changing the interface.
"""

from bisect import bisect_left, bisect_right, insort_left
from collections.abc import Sequence
from datetime import datetime, timedelta

from epimemer.core.types import EdgeType, NodeEdge, Timeline, Timepoint
from epimemer.pipelines.timeline.temporal import detect_temporal_expressions


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
    """Every dated timepoint overlapping `[start, end]`, in chronological order.

    A point overlaps when its first moment is at or before `end` and its last
    moment is at or after `start`. An instant's last moment is its `start`, an
    interval's is its `end`, which is the same reading of "last moment" that
    `compare_intervals` in `core/temporal.py` uses. Vague points have no
    coordinate, so a range query cannot answer for them and leaves them out.

    **Intervals that began before the window come back in their place**, ahead
    of the points inside it rather than appended after them. A caller reading
    the list as a chronology would otherwise have to re-sort it, and the war
    that was already running would arrive after the treaty that ended it.

    **How far back the search goes.** Points are sorted by `start`, so one
    bisect finds the far edge of the window. The near edge needs a walk
    backwards, because an interval that began long ago can still be running.
    That walk has an exact bound: nothing starting more than the longest
    interval on this timeline before `start` can reach the window. The longest
    interval is measured in the same pass that gathers the dated points, so the
    bound costs nothing to have, and a timeline whose intervals are all short
    skips its whole earlier history rather than testing every point in it, as
    this function used to.

    Sorting is by `start` and is done here rather than assumed: the record is
    kept in that order by `reorder_timepoints`, but a chronology the caller was
    promised should not depend on it. On an already-sorted list the sort is a
    single pass.
    """
    dated = sorted(
        (tp for tp in timeline.timepoints if tp.start is not None),
        key=lambda tp: tp.start,
    )
    if not dated:
        return []

    longest = max(
        ((tp.end - tp.start) for tp in dated if tp.end is not None),
        default=timedelta(0),
    )

    starts = [tp.start for tp in dated]
    # The first point that begins after the window: nothing from here on can
    # overlap, since every later point begins later still.
    stop = bisect_right(starts, end)
    # The earliest point that could still be running when the window opens.
    first = bisect_left(starts, start - longest)

    return [tp for tp in dated[first:stop] if (tp.end if tp.end is not None else tp.start) >= start]


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


# --- Proposing timepoints from node content ---


def _timepoint_key(tp: Timepoint) -> tuple:
    """What makes two timepoints the same point in time.

    The id cannot do this job: the whole purpose is to recognise that a
    timepoint proposed now is the one already on the timeline.
    """
    return (tp.start, tp.end, tp.label)


def propose_timepoints(
    nodes: Sequence[tuple[str, str]],
    timeline: Timeline,
) -> tuple[Timeline, list[NodeEdge], int]:
    """Read `(node_id, content)` pairs and link them to timepoints they name.

    Returns the extended timeline, the `TIMELINK` edges to write, and how many
    timepoints are new. All three belong to the same atomic write: an edge whose
    timepoint was never stored is a dangling reference the read path resolves to
    an empty row rather than an error.

    Timepoints are deduplicated by their resolved value, both against each other
    and against what the timeline already holds — two nodes naming 1897 are two
    things said about one point in time, not two coincident marks. This is also
    what makes re-ingesting a document idempotent as to timepoints.
    """
    known = {_timepoint_key(tp): tp.id for tp in timeline.timepoints}
    edges: list[NodeEdge] = []
    added = 0

    for node_id, content in nodes:
        for found in detect_temporal_expressions(content):
            key = (found.start, found.end, found.label)
            timepoint_id = known.get(key)
            if timepoint_id is None:
                timeline, timepoint = add_timepoint(
                    timeline,
                    start=found.start,
                    end=found.end,
                    label=found.label,
                    # What the text said, kept for anyone auditing a proposal
                    # that looks wrong. A resolved date needs no label — it
                    # would only repeat the date — so this is not one.
                    metadata={"detected_from": found.text, "proposed_by": "extraction"},
                )
                timepoint_id = timepoint.id
                known[key] = timepoint_id
                added += 1
            edges.append(
                NodeEdge(
                    src_id=node_id,
                    dst_id=timeline.id,
                    type=EdgeType.TIMELINK,
                    metadata={"timepoint_id": timepoint_id},
                )
            )

    return timeline, edges, added
