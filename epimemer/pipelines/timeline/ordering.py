"""Stated order between timepoints: the graph, the checks, and the verdicts.

Everything here is a pure function of a `Timeline` and its inputs, returning a
new `Timeline` and a report. The MCP tools read the record, call one of these,
write the record back and journal the decision, so the reasoning lives in one
place that can be tested without a backend.

Two ideas carry the module. The **ordering graph** is every step of "this came
before that" the timeline knows: a live constraint a source asserted, or an edge
read off two points' own dates. The **checks** are what runs after a write that
could have made the graph impossible, and there are two of them because there
are two ways to be impossible: a loop, and a point squeezed until nothing is
left of it.

Nothing here assumes the coordinate is a date where a comparison would do, apart
from reading `start` and `end` off a timepoint. Ordering and bounds are
comparisons on coordinates, so a timeline on another clock would be a type swap
rather than a rewrite.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from epimemer.core.temporal import IntervalBasis
from epimemer.core.types import (
    ConstraintRetirement,
    JudgeRef,
    OrderingConstraint,
    OrderingEdge,
    TemporalContradiction,
    TemporalResolution,
    Timeline,
    Timepoint,
    contradiction_points,
    disputed_constraint_ids,
)

# Which constraints an edge set is built from. `stated` leaves out the ones a
# judge inferred from tense or context, which answers "what do the sources
# actually say about the order" — the question asked when two accounts disagree.
BasisFilter = Literal["all", "stated"]


# --- The ordering graph ---


def active_timepoints(timeline: Timeline) -> list[Timepoint]:
    """The points a query and a check can see: everything a merge did not retire."""
    return [point for point in timeline.timepoints if point.merged_into is None]


def first_moment(point: Timepoint) -> datetime | None:
    """The earliest moment a point occupies, or None when nothing dates it."""
    return point.start


def last_moment(point: Timepoint) -> datetime | None:
    """The latest moment a point occupies, or None when nothing dates it.

    An interval's is its `end`, an instant's is its `start`. The same reading of
    "last moment" that `compare_intervals` in `core/temporal.py` uses.
    """
    if point.start is None:
        return None
    return point.end if point.end is not None else point.start


def dates_settle_order(earlier: Timepoint, later: Timepoint) -> bool:
    """True when these two points' own dates put `earlier` before `later`.

    The rule is that `earlier`'s last moment is at or before `later`'s first
    moment, **and the same is not true the other way round**. The second half is
    what keeps two points at the very same instant from ordering each other in
    both directions: at that point the dates settle nothing, which is also the
    answer for two intervals that overlap. An answer only where the answer is
    certain, as `compare_intervals` has it.
    """
    earlier_last, later_first = last_moment(earlier), first_moment(later)
    later_last, earlier_first = last_moment(later), first_moment(earlier)
    if earlier_last is None or later_first is None:
        return False
    forwards = earlier_last <= later_first
    backwards = later_last is not None and earlier_first is not None and later_last <= earlier_first
    return forwards and not backwards


def date_derived_edges(points: Sequence[Timepoint]) -> list[OrderingEdge]:
    """Every step two dated points settle between themselves.

    Dates take part in the ordering because the interesting contradiction is the
    one that crosses them: a source asserting "the flood came before the fire"
    when the fire is dated 1897 and the flood 1899 is a disagreement worth
    holding, and a check over constraints alone would not see it.
    """
    dated = [point for point in points if point.start is not None]
    return [
        OrderingEdge(earlier_id=a.id, later_id=b.id)
        for a in dated
        for b in dated
        if a.id != b.id and dates_settle_order(a, b)
    ]


def live_constraints(
    timeline: Timeline,
    *,
    basis: BasisFilter = "all",
) -> list[OrderingConstraint]:
    """The constraints that count: not retired, not disputed, matching `basis`.

    A disputed constraint is excluded from exactly three things, and this is all
    three of them: the closure that `before`, `after` and `between` walk, the
    bounds computation, and the cycle check itself, so a resolved cycle is not
    detected over and over. It is excluded from nothing else, because what the
    sources said is the record.
    """
    disputed = disputed_constraint_ids(timeline.temporal_contradictions)
    present = {point.id for point in active_timepoints(timeline)}
    return [
        constraint
        for constraint in timeline.constraints
        if constraint.retired is None
        and constraint.id not in disputed
        and (basis == "all" or constraint.basis is IntervalBasis.STATED)
        and constraint.earlier_id in present
        and constraint.later_id in present
    ]


class OrderingGraph(BaseModel):
    """The live ordering as adjacency in both directions, built once per call.

    Both directions because bounds need them: what reaches a point gives its
    `earliest`, what it reaches gives its `latest`. Building the edge set in one
    place is also what makes the `basis` filter one branch rather than several.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    points: dict[str, Timepoint] = Field(default_factory=dict)
    forward: dict[str, list[OrderingEdge]] = Field(default_factory=dict)
    backward: dict[str, list[OrderingEdge]] = Field(default_factory=dict)


def build_graph(
    timeline: Timeline,
    *,
    basis: BasisFilter = "all",
    extra: Sequence[OrderingEdge] = (),
) -> OrderingGraph:
    """The live constraints plus the date-derived edges, as a walkable graph.

    `extra` is for a step that is not in the record yet: the cycle check asks
    whether a constraint would close a loop before it decides what to store.
    """
    points = {point.id: point for point in active_timepoints(timeline)}
    edges = [
        OrderingEdge(
            earlier_id=constraint.earlier_id,
            later_id=constraint.later_id,
            constraint_id=constraint.id,
        )
        for constraint in live_constraints(timeline, basis=basis)
    ]
    edges += date_derived_edges(list(points.values()))
    edges += [edge for edge in extra if edge.earlier_id in points and edge.later_id in points]

    graph = OrderingGraph(points=points)
    for edge in edges:
        graph.forward.setdefault(edge.earlier_id, []).append(edge)
        graph.backward.setdefault(edge.later_id, []).append(edge)
    return graph


# --- Walking it ---


def _search(
    graph: OrderingGraph,
    start: str,
    *,
    backwards: bool = False,
) -> dict[str, OrderingEdge | None]:
    """Breadth-first from `start`, keeping the edge each point was reached by.

    The parent pointers are the whole reason this is written out rather than
    done with a set: a cycle has to be reported as a path, and a bound has to
    name the constraints that produced it.
    """
    adjacency = graph.backward if backwards else graph.forward
    reached: dict[str, OrderingEdge | None] = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, ()):
            nxt = edge.earlier_id if backwards else edge.later_id
            if nxt not in reached:
                reached[nxt] = edge
                queue.append(nxt)
    return reached


def _path_to(
    reached: dict[str, OrderingEdge | None],
    start: str,
    target: str,
    *,
    backwards: bool = False,
) -> list[OrderingEdge]:
    """The edges from `start` to `target`, read back off the parent pointers."""
    path: list[OrderingEdge] = []
    cursor = target
    while cursor != start:
        edge = reached[cursor]
        if edge is None:
            break
        path.append(edge)
        cursor = edge.later_id if backwards else edge.earlier_id
    return path if backwards else list(reversed(path))


def path_between(graph: OrderingGraph, src: str, dst: str) -> list[OrderingEdge] | None:
    """The steps taking `src` to `dst`, or None when it does not reach it."""
    if src == dst:
        return []
    reached = _search(graph, src)
    if dst not in reached:
        return None
    return _path_to(reached, src, dst)


def points_after(graph: OrderingGraph, point_id: str) -> list[str]:
    """Every point this one reaches: what the order puts after it."""
    return [found for found in _search(graph, point_id) if found != point_id]


def points_before(graph: OrderingGraph, point_id: str) -> list[str]:
    """Every point that reaches this one: what the order puts before it."""
    return [found for found in _search(graph, point_id, backwards=True) if found != point_id]


def points_between(graph: OrderingGraph, after_id: str, before_id: str) -> list[str]:
    """The intersection of "after A" and "before B".

    A point that neither reaches is not between them: the order has to put it on
    both sides before this answers for it.
    """
    later = set(points_after(graph, after_id))
    earlier = set(points_before(graph, before_id))
    return sorted(later & earlier)


# --- Cycles ---


def self_ordering_refusal(earlier_id: str, later_id: str) -> str | None:
    """Why a pair naming one point twice is malformed, or None if it is not.

    Refused outright rather than recorded as a one-edge cycle. A point cannot
    precede itself; that is a bad request, not a disagreement between sources.
    """
    if earlier_id != later_id:
        return None
    return (
        f"a point cannot come before itself: '{earlier_id}' was given as both "
        f"the earlier and the later point."
    )


def cycle_closed_by(
    graph: OrderingGraph,
    edge: OrderingEdge,
) -> list[OrderingEdge] | None:
    """The loop `edge` would close, or None when it closes none.

    One traversal from the later point looking for the earlier one. The loop is
    reported as a path so a judge reading it can see which sources are involved
    and where they meet.
    """
    back = path_between(graph, edge.later_id, edge.earlier_id)
    return None if back is None else [edge, *back]


def find_any_cycle(graph: OrderingGraph) -> list[OrderingEdge] | None:
    """Any loop in the live graph, as a path, or None when there is none.

    Used after a *dated* point is added, where one new date can create edges
    against every other dated point and any of them can complete a loop with
    existing constraints. Checking each new edge separately would be a traversal
    per edge; one depth-first walk answers for all of them.

    A loop always passes through at least one constraint, because the
    date-derived edges alone cannot contain one: each step moves strictly
    forward in time unless the dates settle nothing, in which case there is no
    step.
    """
    colour: dict[str, int] = {}  # 0 = on the stack, 1 = finished
    for origin in graph.points:
        if origin in colour:
            continue
        stack: list[tuple[str, Iterable[OrderingEdge]]] = [
            (origin, iter(graph.forward.get(origin, ())))
        ]
        trail: list[OrderingEdge] = []
        colour[origin] = 0
        while stack:
            current, pending = stack[-1]
            step = next(pending, None)
            if step is None:
                colour[current] = 1
                stack.pop()
                if trail:
                    trail.pop()
                continue
            nxt = step.later_id
            if colour.get(nxt) == 0:
                loop = [step]
                for edge in reversed(trail):
                    if loop[-1].earlier_id == nxt:
                        break
                    loop.append(edge)
                return list(reversed(loop))
            if nxt not in colour:
                colour[nxt] = 0
                trail.append(step)
                stack.append((nxt, iter(graph.forward.get(nxt, ()))))
    return None


# --- Bounds ---


class Bounds(BaseModel):
    """Where the order puts a point that nothing dates.

    Either end can be absent, and an absent end is a real answer rather than a
    failure: a point with only successors is "before 1897, we do not know when".
    Bounds are never written to the record. They are computed per query and
    recomputed next time, so retiring a constraint takes its bound away with it.
    """

    earliest: datetime | None = None
    latest: datetime | None = None
    # The constraints on the two paths that produced the bounds, which is what a
    # crossed-bounds contradiction has to name.
    earliest_path: list[OrderingEdge] = Field(default_factory=list)
    latest_path: list[OrderingEdge] = Field(default_factory=list)

    @property
    def crossed(self) -> bool:
        return self.earliest is not None and self.latest is not None and self.earliest > self.latest


def bounds_for(graph: OrderingGraph, point_id: str) -> Bounds:
    """`earliest` and `latest` for one point, over the live graph.

    `earliest` is the latest last moment among the dated points that reach it;
    `latest` is the earliest first moment among the dated points it reaches. The
    winning point's path is kept, because a crossing has to say which
    constraints squeezed it.
    """
    behind = _search(graph, point_id, backwards=True)
    ahead = _search(graph, point_id)

    earliest: datetime | None = None
    earliest_from: str | None = None
    for reached in behind:
        moment = last_moment(graph.points[reached]) if reached != point_id else None
        if moment is not None and (earliest is None or moment > earliest):
            earliest, earliest_from = moment, reached

    latest: datetime | None = None
    latest_from: str | None = None
    for reached in ahead:
        moment = first_moment(graph.points[reached]) if reached != point_id else None
        if moment is not None and (latest is None or moment < latest):
            latest, latest_from = moment, reached

    return Bounds(
        earliest=earliest,
        latest=latest,
        earliest_path=(
            _path_to(behind, point_id, earliest_from, backwards=True)
            if earliest_from is not None
            else []
        ),
        latest_path=(_path_to(ahead, point_id, latest_from) if latest_from is not None else []),
    )


# --- Recording what the checks find ---


def _constraint_ids(edges: Sequence[OrderingEdge]) -> list[str]:
    seen: list[str] = []
    for edge in edges:
        if edge.constraint_id is not None and edge.constraint_id not in seen:
            seen.append(edge.constraint_id)
    return seen


def _sources_of(timeline: Timeline, constraint_ids: Sequence[str]) -> list[str]:
    by_id = {constraint.id: constraint for constraint in timeline.constraints}
    seen: list[str] = []
    for constraint_id in constraint_ids:
        constraint = by_id.get(constraint_id)
        if constraint is not None and constraint.source_id not in seen:
            seen.append(constraint.source_id)
    return seen


def _contradiction(
    timeline: Timeline,
    *,
    kind: Literal["cycle", "crossed_bounds"],
    edges: Sequence[OrderingEdge],
    point_id: str | None,
    at: datetime,
) -> TemporalContradiction:
    constraint_ids = _constraint_ids(edges)
    return TemporalContradiction(
        kind=kind,
        edges=list(edges),
        point_id=point_id,
        constraint_ids=constraint_ids,
        sources=_sources_of(timeline, constraint_ids),
        found_at=at,
    )


def _with_contradiction(
    timeline: Timeline,
    contradiction: TemporalContradiction,
) -> Timeline:
    return timeline.model_copy(
        update={
            "temporal_contradictions": [*timeline.temporal_contradictions, contradiction],
        }
    )


def run_checks(
    timeline: Timeline,
    *,
    at: datetime,
    basis: BasisFilter = "all",
) -> tuple[Timeline, list[TemporalContradiction]]:
    """Both checks, until the live graph holds no impossibility it can see.

    A recorded contradiction disputes at least one constraint, and a disputed
    constraint leaves the live graph, so each pass has strictly fewer live
    constraints than the last and the loop cannot run away. That is also why
    each pass rebuilds the graph rather than reusing the last one.

    Crossed bounds are checked for the points nothing dates, which are the ones
    whose position is derived. A dated point keeps its date whatever the order
    says about it, and disagreement between a date and a constraint shows up as
    a cycle when the dates are certain enough to settle it.
    """
    opened: list[TemporalContradiction] = []
    for _ in range(len(timeline.constraints) + 1):
        graph = build_graph(timeline, basis=basis)

        loop = find_any_cycle(graph)
        if loop is not None and _constraint_ids(loop):
            found = _contradiction(timeline, kind="cycle", edges=loop, point_id=None, at=at)
            timeline = _with_contradiction(timeline, found)
            opened.append(found)
            continue

        crossing = next(
            (
                (point.id, bounds)
                for point in active_timepoints(timeline)
                if point.start is None
                for bounds in [bounds_for(graph, point.id)]
                if bounds.crossed
            ),
            None,
        )
        if crossing is None:
            break
        point_id, bounds = crossing
        found = _contradiction(
            timeline,
            kind="crossed_bounds",
            edges=[*bounds.earliest_path, *bounds.latest_path],
            point_id=point_id,
            at=at,
        )
        timeline = _with_contradiction(timeline, found)
        opened.append(found)
    return timeline, opened


def contested_points(timeline: Timeline) -> dict[str, str]:
    """Point id to the id of an open contradiction that touches it.

    A point on a disputed cycle gets no derived position and is reported as
    contested. A *dated* point in one keeps its date and is marked contested
    beside it: a date is what a source stated about that point, a constraint is
    what a source stated about a pair, and a dispute between them is a reason to
    stop trusting the derived order rather than to remove a date.

    **Crossed bounds contest one point, not the paths that squeezed it.** The
    two dated points at the ends of those paths are not in dispute with each
    other; what cannot hold is the position the order gives the point between
    them. Marking them would report a well-dated point as doubtful for having
    bounded something else. A cycle is the other case: every point on the loop
    is part of the assertion that cannot hold.
    """
    contested: dict[str, str] = {}
    for contradiction in timeline.temporal_contradictions:
        if not contradiction.is_open:
            continue
        touched = (
            {contradiction.point_id}
            if contradiction.kind == "crossed_bounds" and contradiction.point_id
            else contradiction_points(contradiction)
        )
        for point_id in touched:
            contested.setdefault(point_id, contradiction.id)
    return contested


def clear_holds(timeline: Timeline, touched: Iterable[str]) -> Timeline:
    """Un-hold every open contradiction touching one of `touched`.

    A hold says nothing on hand settles the disagreement, so it waits for
    evidence, and a constraint or a dated point about any point in it is that
    evidence. Nothing else clears a hold: this is not a snooze, and nothing
    reopens one on a schedule.
    """
    points = set(touched)
    updated = [
        (
            contradiction.model_copy(update={"held": False})
            if contradiction.held
            and contradiction.is_open
            and contradiction_points(contradiction) & points
            else contradiction
        )
        for contradiction in timeline.temporal_contradictions
    ]
    return timeline.model_copy(update={"temporal_contradictions": updated})


# --- Adding constraints ---


class ConstraintOutcome(BaseModel):
    """What became of one requested pair."""

    earlier_id: str
    later_id: str
    constraint_id: str | None = None
    created: bool = False
    refused: str | None = None


class OrderingReport(BaseModel):
    """What `order_timepoints` did, and what it disturbed."""

    outcomes: list[ConstraintOutcome] = Field(default_factory=list)
    opened: list[TemporalContradiction] = Field(default_factory=list)
    refused: str | None = None


def add_constraints(
    timeline: Timeline,
    pairs: Sequence[tuple[str, str]],
    *,
    source_id: str,
    basis: IntervalBasis,
    because: str | None = None,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, OrderingReport]:
    """Record one source asserting an order, and check what it did to the graph.

    Idempotent on `(earlier_id, later_id, source_id)`: a source asserting the
    same order twice has said one thing, and the second telling returns the
    first constraint rather than a duplicate. A retired constraint does not
    block a fresh one, because a judge withdrawing an old reading does not stop
    a new reading from being recorded.

    Opening a contradiction does not fail the call. The insert still succeeds
    and the graph holds the disagreement; it does not decide it.
    """
    present = {point.id for point in active_timepoints(timeline)}
    outcomes: list[ConstraintOutcome] = []
    touched: set[str] = set()

    for earlier_id, later_id in pairs:
        refusal = self_ordering_refusal(earlier_id, later_id)
        if refusal is None:
            missing = [pid for pid in (earlier_id, later_id) if pid not in present]
            if missing:
                refusal = (
                    f"timeline '{timeline.id}' has no active point "
                    f"{', '.join(repr(pid) for pid in missing)}."
                )
        if refusal is not None:
            outcomes.append(
                ConstraintOutcome(earlier_id=earlier_id, later_id=later_id, refused=refusal)
            )
            continue

        existing = next(
            (
                constraint
                for constraint in timeline.constraints
                if constraint.retired is None
                and constraint.earlier_id == earlier_id
                and constraint.later_id == later_id
                and constraint.source_id == source_id
            ),
            None,
        )
        if existing is not None:
            outcomes.append(
                ConstraintOutcome(
                    earlier_id=earlier_id,
                    later_id=later_id,
                    constraint_id=existing.id,
                    created=False,
                )
            )
            continue

        constraint = OrderingConstraint(
            earlier_id=earlier_id,
            later_id=later_id,
            source_id=source_id,
            basis=basis,
            because=because,
            judged_by=judge,
            asserted_at=at,
        )
        timeline = timeline.model_copy(update={"constraints": [*timeline.constraints, constraint]})
        touched |= {earlier_id, later_id}
        outcomes.append(
            ConstraintOutcome(
                earlier_id=earlier_id,
                later_id=later_id,
                constraint_id=constraint.id,
                created=True,
            )
        )

    timeline = clear_holds(timeline, touched)
    timeline, opened = run_checks(timeline, at=at)
    return timeline, OrderingReport(outcomes=outcomes, opened=opened)


# --- Verdicts ---


class LinkMove(BaseModel):
    """A `TIMELINK` the caller has to retire and rewrite.

    Named rather than performed, because a timelink is an edge and this module
    knows only the timeline record. The tool does the edge work, so the decision
    stays testable without a backend.
    """

    node_id: str
    from_point_id: str
    to_point_id: str


class VerdictReport(BaseModel):
    """What a verdict did, in the terms a judge asked the question in."""

    refused: str | None = None
    contradiction_id: str | None = None
    returned_to_live: list[str] = Field(default_factory=list)
    # Constraint id to the other open contradiction still holding it disputed.
    still_disputed: dict[str, str] = Field(default_factory=dict)
    new_timepoint_id: str | None = None
    moved_constraint_ids: list[str] = Field(default_factory=list)
    left_on_original: list[str] = Field(default_factory=list)
    link_moves: list[LinkMove] = Field(default_factory=list)
    opened: list[TemporalContradiction] = Field(default_factory=list)


def _find_contradiction(
    timeline: Timeline,
    contradiction_id: str,
) -> TemporalContradiction | None:
    return next(
        (
            contradiction
            for contradiction in timeline.temporal_contradictions
            if contradiction.id == contradiction_id
        ),
        None,
    )


def _replace_contradiction(
    timeline: Timeline,
    updated: TemporalContradiction,
) -> Timeline:
    return timeline.model_copy(
        update={
            "temporal_contradictions": [
                updated if contradiction.id == updated.id else contradiction
                for contradiction in timeline.temporal_contradictions
            ]
        }
    )


def _freed(
    timeline: Timeline,
    closed: TemporalContradiction,
) -> tuple[list[str], dict[str, str]]:
    """Which of a closed contradiction's constraints are live again, and which are not."""
    freed: list[str] = []
    still: dict[str, str] = {}
    for constraint_id in closed.constraint_ids:
        holder = next(
            (
                other.id
                for other in timeline.temporal_contradictions
                if other.id != closed.id and other.is_open and constraint_id in other.constraint_ids
            ),
            None,
        )
        if holder is None:
            freed.append(constraint_id)
        else:
            still[constraint_id] = holder
    return freed, still


def _retire_constraint(
    timeline: Timeline,
    constraint_id: str,
    retirement: ConstraintRetirement,
) -> Timeline:
    return timeline.model_copy(
        update={
            "constraints": [
                (
                    constraint.model_copy(update={"retired": retirement})
                    if constraint.id == constraint_id
                    else constraint
                )
                for constraint in timeline.constraints
            ]
        }
    )


def retire_constraint(
    timeline: Timeline,
    *,
    contradiction_id: str,
    constraint_id: str,
    because: str,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, VerdictReport]:
    """One constraint should not be believed, so the disagreement is over.

    The three reasons this verdict is for are the real ones: the source was
    misread, the source is unreliable, or the order it gives is narrative rather
    than chronological. Which one it was goes in `because`, in the judge's own
    words, rather than into a list of codes that would have to be right in
    advance.

    The constraint is kept with the reason and the judge, the contradiction
    closes, and every other constraint in it returns to live unless another open
    contradiction still holds it.
    """
    contradiction = _find_contradiction(timeline, contradiction_id)
    if contradiction is None:
        return timeline, VerdictReport(
            refused=f"timeline '{timeline.id}' has no contradiction '{contradiction_id}'."
        )
    if not contradiction.is_open:
        return timeline, VerdictReport(
            refused=f"contradiction '{contradiction_id}' is already answered.",
            contradiction_id=contradiction_id,
        )
    if constraint_id not in contradiction.constraint_ids:
        return timeline, VerdictReport(
            refused=(
                f"constraint '{constraint_id}' is not one of the constraints in "
                f"contradiction '{contradiction_id}'."
            ),
            contradiction_id=contradiction_id,
        )

    timeline = _retire_constraint(
        timeline,
        constraint_id,
        ConstraintRetirement(
            because=because, judged_by=judge, at=at, contradiction_id=contradiction_id
        ),
    )
    closed = contradiction.model_copy(
        update={
            "held": False,
            "resolutions": [
                *contradiction.resolutions,
                TemporalResolution(
                    verdict="retire_constraint",
                    because=because,
                    judged_by=judge,
                    at=at,
                    constraint_id=constraint_id,
                ),
            ],
        }
    )
    timeline = _replace_contradiction(timeline, closed)
    freed, still = _freed(timeline, closed)
    timeline, opened = run_checks(timeline, at=at)
    return timeline, VerdictReport(
        contradiction_id=contradiction_id,
        returned_to_live=[cid for cid in freed if cid != constraint_id],
        still_disputed=still,
        opened=opened,
    )


def hold(
    timeline: Timeline,
    *,
    contradiction_id: str,
    because: str,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, VerdictReport]:
    """The sources genuinely disagree and nothing on hand settles it.

    The contradiction stays open and its constraints stay disputed, so the point
    is still reported as contested and still has no position. What changes is
    that `reflect` stops nominating it until new evidence arrives.
    """
    contradiction = _find_contradiction(timeline, contradiction_id)
    if contradiction is None:
        return timeline, VerdictReport(
            refused=f"timeline '{timeline.id}' has no contradiction '{contradiction_id}'."
        )
    if not contradiction.is_open:
        return timeline, VerdictReport(
            refused=f"contradiction '{contradiction_id}' is already answered.",
            contradiction_id=contradiction_id,
        )
    held = contradiction.model_copy(
        update={
            "held": True,
            "resolutions": [
                *contradiction.resolutions,
                TemporalResolution(verdict="hold", because=because, judged_by=judge, at=at),
            ],
        }
    )
    return _replace_contradiction(timeline, held), VerdictReport(contradiction_id=contradiction_id)


def _repoint(
    constraint: OrderingConstraint,
    *,
    old_id: str,
    new_id: str,
    because: str,
    judge: JudgeRef | None,
    at: datetime,
    contradiction_id: str | None = None,
) -> tuple[OrderingConstraint, OrderingConstraint]:
    """The retired original and its replacement, pointing at `new_id`.

    Re-pointing is a retirement plus a fresh constraint rather than an edit,
    because no constraint's stored content is ever changed. The replacement
    keeps the source, the basis and the judge who read the source: that reading
    has not been withdrawn, only attached to a different point.
    """
    replacement = OrderingConstraint(
        earlier_id=new_id if constraint.earlier_id == old_id else constraint.earlier_id,
        later_id=new_id if constraint.later_id == old_id else constraint.later_id,
        source_id=constraint.source_id,
        basis=constraint.basis,
        because=constraint.because,
        judged_by=constraint.judged_by,
        asserted_at=at,
    )
    retired = constraint.model_copy(
        update={
            "retired": ConstraintRetirement(
                because=because,
                judged_by=judge,
                at=at,
                contradiction_id=contradiction_id,
                superseded_by=replacement.id,
            )
        }
    )
    return retired, replacement


def split_timepoint(
    timeline: Timeline,
    *,
    contradiction_id: str,
    point_id: str,
    constraints_to_move: Sequence[str],
    nodes_to_move: Sequence[str] = (),
    because: str,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, VerdictReport]:
    """Both sources are right, and the point is really two events.

    The sources are each correct about a different occurrence of the thing the
    point names, so the point is what is wrong rather than either constraint. A
    new point takes the original's label, the named constraints move to it, and
    both stay live.

    **The new point takes the label and not the dates.** Two occurrences of one
    named thing are not the same moment, so copying a date onto the second would
    assert something no source said. That is also why a point with no label
    cannot be split: there would be nothing to carry over, and inventing a
    label is the fabrication this whole design refuses.

    The facts the judge names move with it, each `TIMELINK` retired and
    rewritten. Nodes the judge does not name stay on the original. Facts move
    one by one, by a decision, because a fact's date is part of what it claims.
    """
    contradiction = _find_contradiction(timeline, contradiction_id)
    if contradiction is None:
        return timeline, VerdictReport(
            refused=f"timeline '{timeline.id}' has no contradiction '{contradiction_id}'."
        )
    if not contradiction.is_open:
        return timeline, VerdictReport(
            refused=f"contradiction '{contradiction_id}' is already answered.",
            contradiction_id=contradiction_id,
        )

    original = next((point for point in active_timepoints(timeline) if point.id == point_id), None)
    if original is None:
        return timeline, VerdictReport(
            refused=f"timeline '{timeline.id}' has no active point '{point_id}'.",
            contradiction_id=contradiction_id,
        )
    if original.label is None or not original.label.strip():
        return timeline, VerdictReport(
            refused=(
                f"point '{point_id}' has no label, so a point split off from it "
                f"would have nothing written on it. Dates are not copied onto a "
                f"split, because two occurrences of one thing are not one moment."
            ),
            contradiction_id=contradiction_id,
        )

    by_id = {constraint.id: constraint for constraint in timeline.constraints}
    wanted = list(dict.fromkeys(constraints_to_move))
    bad = [
        cid
        for cid in wanted
        if cid not in by_id
        or by_id[cid].retired is not None
        or point_id not in (by_id[cid].earlier_id, by_id[cid].later_id)
    ]
    if bad:
        return timeline, VerdictReport(
            refused=(
                f"these constraints cannot move to the split point: "
                f"{', '.join(repr(cid) for cid in bad)}. Each has to be live and "
                f"to name '{point_id}' at one end."
            ),
            contradiction_id=contradiction_id,
        )

    fresh = Timepoint(label=original.label, split_from=point_id)
    constraints = list(timeline.constraints)
    for index, constraint in enumerate(constraints):
        if constraint.id in wanted:
            retired, replacement = _repoint(
                constraint,
                old_id=point_id,
                new_id=fresh.id,
                because=because,
                judge=judge,
                at=at,
                contradiction_id=contradiction_id,
            )
            constraints[index] = retired
            constraints.append(replacement)

    timeline = timeline.model_copy(
        update={
            "timepoints": [*timeline.timepoints, fresh],
            "constraints": constraints,
        }
    )
    moved_nodes = list(dict.fromkeys(nodes_to_move))
    closed = contradiction.model_copy(
        update={
            "held": False,
            "resolutions": [
                *contradiction.resolutions,
                TemporalResolution(
                    verdict="not_the_same_event",
                    because=because,
                    judged_by=judge,
                    at=at,
                    point_id=point_id,
                    new_timepoint_id=fresh.id,
                    moved_constraint_ids=wanted,
                    moved_node_ids=moved_nodes,
                ),
            ],
        }
    )
    timeline = _replace_contradiction(timeline, closed)
    freed, still = _freed(timeline, closed)
    timeline, opened = run_checks(timeline, at=at)
    return timeline, VerdictReport(
        contradiction_id=contradiction_id,
        returned_to_live=freed,
        still_disputed=still,
        new_timepoint_id=fresh.id,
        moved_constraint_ids=wanted,
        link_moves=[
            LinkMove(node_id=node_id, from_point_id=point_id, to_point_id=fresh.id)
            for node_id in moved_nodes
        ],
        opened=opened,
    )


def merge_timepoints(
    timeline: Timeline,
    *,
    survivor_id: str,
    merged_id: str,
    linked_node_ids: Sequence[str] = (),
    because: str,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, VerdictReport]:
    """Two points are one moment, so one of them retires into the other.

    The reverse of a split, and not an answer to a contradiction: extraction
    makes one point per distinct phrase, so "the launch" and "the go-live" from
    two documents are two points a judge may later recognise as one event.

    Every constraint naming the retired point moves to the survivor and every
    link to it moves as well, **all of them rather than a chosen subset**: a
    merge asserts the two are one, so every fact about either is a fact about
    it. One refusal: a live constraint ordering the two against each other is a
    source saying they are not one moment, and it has to be retired first.
    """
    if survivor_id == merged_id:
        return timeline, VerdictReport(
            refused="a point cannot be merged into itself: name two different points."
        )
    present = {point.id: point for point in active_timepoints(timeline)}
    missing = [pid for pid in (survivor_id, merged_id) if pid not in present]
    if missing:
        return timeline, VerdictReport(
            refused=(
                f"timeline '{timeline.id}' has no active point "
                f"{', '.join(repr(pid) for pid in missing)}."
            )
        )

    ordering = next(
        (
            constraint
            for constraint in live_constraints(timeline)
            if {constraint.earlier_id, constraint.later_id} == {survivor_id, merged_id}
        ),
        None,
    )
    if ordering is not None:
        return timeline, VerdictReport(
            refused=(
                f"constraint '{ordering.id}' orders these two points against each "
                f"other, on source '{ordering.source_id}': a source that ordered "
                f"them said they are not one moment. Retire that constraint with a "
                f"reason before merging."
            )
        )

    constraints = list(timeline.constraints)
    moved: list[str] = []
    for index, constraint in enumerate(constraints):
        if constraint.retired is not None:
            continue
        if merged_id not in (constraint.earlier_id, constraint.later_id):
            continue
        retired, replacement = _repoint(
            constraint,
            old_id=merged_id,
            new_id=survivor_id,
            because=because,
            judge=judge,
            at=at,
        )
        constraints[index] = retired
        constraints.append(replacement)
        moved.append(constraint.id)

    timeline = timeline.model_copy(
        update={
            "timepoints": [
                point.model_copy(update={"merged_into": survivor_id})
                if point.id == merged_id
                else point
                for point in timeline.timepoints
            ],
            "constraints": constraints,
        }
    )
    timeline, opened = run_checks(timeline, at=at)
    return timeline, VerdictReport(
        moved_constraint_ids=moved,
        link_moves=[
            LinkMove(node_id=node_id, from_point_id=merged_id, to_point_id=survivor_id)
            for node_id in dict.fromkeys(linked_node_ids)
        ],
        opened=opened,
    )
