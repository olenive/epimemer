"""What recurs on a timeline: the rules, their occurrences, and the exceptions.

Everything here is a pure function of a `Recurrence` or a `Timeline`, returning
occurrences or a new timeline. The MCP tools read the record, call one of these,
write the record back and journal the decision, so the reasoning lives in one
place that can be tested without a backend.

**Occurrences are computed and never stored.** A rule plus a window gives a
list; keeping the list would make the rule and its expansion two things that can
disagree. The only occurrences that become records are the ones somebody
materialises, and those are ordinary timepoints that carry the rule and the
start it gave them.

**Nothing walks a rule from its beginning.** A window is entered by arithmetic
for a periodic rule and by `after()` for a calendar one, and the nearest
occurrence to a moment is one division or one `before()`/`after()` pair. The
obvious implementation of nearest is "enumerate, then sort", which works on
every small example and hangs on a daily rule anchored a century back.

A periodic rule is arithmetic on coordinates and knows nothing about dates
beyond subtracting them, so a timeline on another clock would be a type swap
rather than a rewrite. Only `CalendarRule` is about months and weekdays.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from pydantic import BaseModel, Field

from epimemer.core.types import (
    BoundChange,
    CalendarRule,
    JudgeRef,
    PeriodicRule,
    Recurrence,
    RecurrenceBounds,
    RecurrenceException,
    RecurrenceExceptionKind,
    RecurrenceRule,
    Timeline,
    Timepoint,
)

# How many occurrences one rule may return in one call. Without a cap, a
# plausible query ("everything this daily rule did in the last century") would
# enumerate thirty-six thousand marks into a tool response, and an unbounded
# window would enumerate without end. When the cap fires the answer says so and
# names the window it actually covered, so the rest can be asked for.
OCCURRENCE_CAP = 200

# How many occurrences `add_recurrence` shows back. An rrule string is easy to
# mistype in a way that parses, and without a preview the mistake is invisible
# until someone queries a window months later.
PREVIEW_OCCURRENCES = 5


class Occurrence(BaseModel):
    """One occurrence of a rule, as a caller reads it.

    `occurrence_start` is the identity: the start the rule produced, before any
    move. `start` is where it actually is. The two differ only for a moved
    occurrence, and the identity has to survive the move, or materialising it
    before and after would produce two points for one occurrence.
    """

    recurrence_id: str
    occurrence_start: datetime
    start: datetime
    end: datetime | None = None
    cancelled: bool = False
    moved: bool = False


class OccurrenceWindow(BaseModel):
    """What one rule produced inside one window, and how much of it was read.

    `truncated` says the cap fired. `covered_start` and `covered_end` are the
    window actually enumerated, which is the part a caller needs in order to ask
    for the rest: on a truncated answer `covered_end` is the last occurrence
    returned rather than the window they asked for.
    """

    recurrence_id: str
    occurrences: list[Occurrence] = Field(default_factory=list)
    truncated: bool = False
    covered_start: datetime | None = None
    covered_end: datetime | None = None


# --- Clocks ---
#
# A calendar rule's occurrences carry whatever `DTSTART` gave them, which may be
# naive, and comparing a naive datetime with an aware one raises rather than
# answering. So every moment is converted into the rule's own clock before it is
# handed to the rule, and every answer is converted back, with a naive rule read
# as UTC. Doing it in one pair of functions is what keeps that out of the rest.


def _rule_clock(recurrence: Recurrence) -> tzinfo | None:
    rule = recurrence.rule
    if isinstance(rule, PeriodicRule):
        return rule.anchor.tzinfo
    first = next(iter(_calendar(rule)), None)
    return None if first is None else first.tzinfo


def _to_clock(moment: datetime, clock: tzinfo | None) -> datetime:
    if clock is None:
        return (
            moment.replace(tzinfo=None)
            if moment.tzinfo is None
            else moment.astimezone(UTC).replace(tzinfo=None)
        )
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _from_clock(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _calendar(rule: CalendarRule):
    from dateutil.rrule import rrulestr

    return rrulestr(rule.rrule)


# --- The grid a rule lays down, before bounds and exceptions ---


def _grid_at_or_before(rule: RecurrenceRule, moment: datetime) -> datetime | None:
    """The last start the rule produces at or before `moment`, in the rule's clock."""
    if isinstance(rule, PeriodicRule):
        # Integer floor division on timedeltas: exact, and one operation
        # whatever the distance from the anchor.
        index = (moment - rule.anchor) // rule.period
        return rule.anchor + index * rule.period
    return _calendar(rule).before(moment, inc=True)


def _grid_after(
    rule: RecurrenceRule, moment: datetime, *, inclusive: bool = False
) -> datetime | None:
    """The first start the rule produces after `moment`, in the rule's clock."""
    if isinstance(rule, PeriodicRule):
        index = (moment - rule.anchor) // rule.period
        candidate = rule.anchor + index * rule.period
        if candidate < moment or (candidate == moment and not inclusive):
            candidate += rule.period
        return candidate
    return _calendar(rule).after(moment, inc=inclusive)


def _grid_produces(rule: RecurrenceRule, moment: datetime) -> bool:
    if isinstance(rule, PeriodicRule):
        return (moment - rule.anchor) % rule.period == timedelta(0)
    return _calendar(rule).before(moment, inc=True) == moment


# --- The grid clipped to the rule's bounds ---


def _limits(
    recurrence: Recurrence, clock: tzinfo | None
) -> tuple[datetime | None, datetime | None]:
    lower = recurrence.bounds.start
    upper = recurrence.effective_end
    return (
        None if lower is None else _to_clock(lower, clock),
        None if upper is None else _to_clock(upper, clock),
    )


def _inside(moment: datetime, lower: datetime | None, upper: datetime | None) -> bool:
    return (lower is None or moment >= lower) and (upper is None or moment <= upper)


def _at_or_before(
    recurrence: Recurrence, moment: datetime, clock: tzinfo | None
) -> datetime | None:
    lower, upper = _limits(recurrence, clock)
    if upper is not None and moment > upper:
        moment = upper
    found = _grid_at_or_before(recurrence.rule, moment)
    if found is None or not _inside(found, lower, upper):
        return None
    return found


def _after(
    recurrence: Recurrence,
    moment: datetime,
    clock: tzinfo | None,
    *,
    inclusive: bool = False,
) -> datetime | None:
    lower, upper = _limits(recurrence, clock)
    found = _grid_after(recurrence.rule, moment, inclusive=inclusive)
    if found is not None and lower is not None and found < lower:
        # Asked from before the rule began: the answer is its first occurrence,
        # reached in one step rather than by stepping up to it.
        found = _grid_after(recurrence.rule, lower, inclusive=True)
    if found is None or not _inside(found, lower, upper):
        return None
    return found


# --- Exceptions ---


def _exception_for(
    recurrence: Recurrence, occurrence_start: datetime
) -> RecurrenceException | None:
    """The exception recorded against this occurrence, if one was.

    The last match wins. Writing one replaces the entry for that occurrence, so
    there is normally one, and reading the last is what makes a stale record
    written some other way behave the same as a fresh one.
    """
    found = None
    for exception in recurrence.exceptions:
        if exception.occurrence_start == occurrence_start:
            found = exception
    return found


def _occurrence(recurrence: Recurrence, grid_start: datetime) -> Occurrence:
    """One grid position, read through whatever exception applies to it."""
    occurrence_start = _from_clock(grid_start)
    exception = _exception_for(recurrence, occurrence_start)
    start = occurrence_start
    cancelled = False
    moved = False
    if exception is not None and exception.kind == "cancelled":
        cancelled = True
    elif exception is not None and exception.moved_to is not None:
        start = exception.moved_to
        moved = True
    return Occurrence(
        recurrence_id=recurrence.id,
        occurrence_start=occurrence_start,
        start=start,
        end=None if recurrence.duration == timedelta(0) else start + recurrence.duration,
        cancelled=cancelled,
        moved=moved,
    )


def _probe_limit(recurrence: Recurrence) -> int:
    """How far nearest and next may step to get past cancelled occurrences.

    Each step past one is caused by a distinct stored cancellation, so the
    exceptions list is an exact bound. That keeps "skip the cancelled ones" from
    turning either of these into the walk they exist to avoid.
    """
    return len(recurrence.exceptions) + 1


# --- Enumeration ---


def occurrences_in_window(
    recurrence: Recurrence,
    *,
    window_start: datetime,
    window_end: datetime,
    cap: int = OCCURRENCE_CAP,
    include_cancelled: bool = False,
) -> OccurrenceWindow:
    """The occurrences whose rule-given start falls inside `[window_start, window_end]`.

    The window is entered directly rather than walked into: one division for a
    periodic rule, one `after()` for a calendar one. The rule's bounds clip the
    window at both ends before anything is enumerated.

    **The window is read against the start the rule gave**, so an occurrence
    moved out of the window is still returned, at its new date, and one moved
    into it from outside is not. The rule is what the window is over; the move
    is a fact about one of its occurrences.

    A cancelled occurrence is left out, or returned marked `cancelled` when the
    caller asks for it. The cap is per rule per call, and when it fires the
    answer says so and names the window it covered.
    """
    clock = _rule_clock(recurrence)
    lower_bound, upper_bound = _limits(recurrence, clock)
    lower = _to_clock(window_start, clock)
    upper = _to_clock(window_end, clock)
    if lower_bound is not None and lower_bound > lower:
        lower = lower_bound
    if upper_bound is not None and upper_bound < upper:
        upper = upper_bound

    found: list[Occurrence] = []
    truncated = False
    cursor = _after(recurrence, lower, clock, inclusive=True)
    steps = 0
    step_limit = max(cap, 0) + len(recurrence.exceptions)
    while cursor is not None and cursor <= upper:
        if len(found) >= max(cap, 0) or steps >= step_limit:
            truncated = True
            break
        steps += 1
        occurrence = _occurrence(recurrence, cursor)
        if include_cancelled or not occurrence.cancelled:
            found.append(occurrence)
        cursor = _after(recurrence, cursor, clock)

    return OccurrenceWindow(
        recurrence_id=recurrence.id,
        occurrences=found,
        truncated=truncated,
        covered_start=_from_clock(lower),
        covered_end=(found[-1].occurrence_start if truncated and found else _from_clock(upper)),
    )


def nearest_occurrence(
    recurrence: Recurrence,
    target: datetime,
    *,
    include_cancelled: bool = False,
) -> Occurrence | None:
    """The occurrence closest to `target`, found without enumerating any.

    One step back and one step forward, and whichever is nearer wins, with the
    earlier one taken on a tie. Closeness is measured on the start the rule gave
    rather than on where a move put it: that start is the occurrence's place in
    the rule, and measuring on it is what keeps this a division rather than a
    search. A moved occurrence found this way is still reported at its new date.
    """
    clock = _rule_clock(recurrence)
    moment = _to_clock(target, clock)

    behind = _skip_cancelled(
        recurrence,
        _at_or_before(recurrence, moment, clock),
        clock,
        forwards=False,
        include_cancelled=include_cancelled,
    )
    ahead = _skip_cancelled(
        recurrence,
        _after(recurrence, moment, clock),
        clock,
        forwards=True,
        include_cancelled=include_cancelled,
    )
    if behind is None:
        return ahead
    if ahead is None:
        return behind
    to_behind = moment - _to_clock(behind.occurrence_start, clock)
    to_ahead = _to_clock(ahead.occurrence_start, clock) - moment
    return behind if to_behind <= to_ahead else ahead


def next_occurrence(
    recurrence: Recurrence,
    after: datetime,
    *,
    include_cancelled: bool = False,
) -> Occurrence | None:
    """The first occurrence strictly after `after`, found without enumerating any.

    `after` is a value the caller resolved, never a clock this function reads.
    "Next" means next after the timeline's own present, and a long call that
    read the clock twice could answer two different questions in one response.
    """
    clock = _rule_clock(recurrence)
    return _skip_cancelled(
        recurrence,
        _after(recurrence, _to_clock(after, clock), clock),
        clock,
        forwards=True,
        include_cancelled=include_cancelled,
    )


def _skip_cancelled(
    recurrence: Recurrence,
    cursor: datetime | None,
    clock: tzinfo | None,
    *,
    forwards: bool,
    include_cancelled: bool,
) -> Occurrence | None:
    """Step past the occurrences the graph records as not having happened."""
    for _ in range(_probe_limit(recurrence)):
        if cursor is None:
            return None
        occurrence = _occurrence(recurrence, cursor)
        if include_cancelled or not occurrence.cancelled:
            return occurrence
        cursor = (
            _after(recurrence, cursor, clock)
            if forwards
            else _at_or_before(recurrence, cursor - timedelta(microseconds=1), clock)
        )
    return None


def resolve_reference_time(timeline: Timeline, *, now: datetime) -> datetime:
    """The moment this timeline calls the present, resolved once and passed down.

    Unset means the wall clock, which arrives as `now` rather than being read
    here: a timeline whose reference time is May 1897, asked for the next
    occurrence of a weekly service, answers in 1897, and a real-world one
    answers today.
    """
    return timeline.reference_time if timeline.reference_time is not None else now


# --- Finding a rule and its materialised occurrences ---


def find_recurrence(timeline: Timeline, recurrence_id: str) -> Recurrence | None:
    return next(
        (recurrence for recurrence in timeline.recurrences if recurrence.id == recurrence_id),
        None,
    )


def materialised_ids(timeline: Timeline, recurrence_id: str) -> dict[datetime, str]:
    """Occurrence start to the timepoint that materialises it, for one rule.

    `(recurrence_id, occurrence_start)` is the materialisation key, so this is
    the whole of what makes materialising idempotent, and what lets a query say
    which occurrences already have a mark.
    """
    return {
        point.occurrence_start: point.id
        for point in timeline.timepoints
        if point.recurrence_id == recurrence_id
        and point.occurrence_start is not None
        and point.merged_into is None
    }


def occurrence_refusal(recurrence: Recurrence, occurrence_start: datetime) -> str | None:
    """Why this occurrence cannot be named, or None when it can.

    Two refusals, and they are the same two for materialising and for recording
    an exception: a start the rule does not produce names nothing, and both
    tools would otherwise write against an occurrence that does not exist.
    """
    clock = _rule_clock(recurrence)
    moment = _to_clock(occurrence_start, clock)
    lower, upper = _limits(recurrence, clock)
    if not _grid_produces(recurrence.rule, moment) or not _inside(moment, lower, upper):
        return (
            f"recurrence '{recurrence.id}' does not produce an occurrence at "
            f"{occurrence_start.isoformat()}. An occurrence is named by the start "
            f"the rule gives it, which `query_timeline` reports as "
            f"`occurrence_start`."
        )
    return None


# --- Writing ---


class MaterialisationReport(BaseModel):
    """What became of a request to turn one occurrence into a point."""

    timepoint_id: str | None = None
    created: bool = False
    refused: str | None = None
    occurrence: Occurrence | None = None


def materialise(
    timeline: Timeline,
    *,
    recurrence_id: str,
    occurrence_start: datetime,
) -> tuple[Timeline, MaterialisationReport]:
    """Turn one occurrence into a real timepoint, once.

    A materialised occurrence is an ordinary instant or interval that also
    carries the rule and the start it gave. It can be linked, ordered and
    disputed like any other point, and it takes part in the ordering checks the
    way any dated point does.

    Idempotent on `(recurrence_id, occurrence_start)`: asking twice returns the
    same point rather than writing a second one. A cancelled occurrence is
    refused, because materialising something the graph records as not having
    happened is always a mistake. A moved one materialises at its moved date and
    is still found by the start the rule gave it.
    """
    from epimemer.pipelines.timeline.functions import add_timepoint

    recurrence = find_recurrence(timeline, recurrence_id)
    if recurrence is None:
        return timeline, MaterialisationReport(
            refused=f"timeline '{timeline.id}' has no recurrence '{recurrence_id}'."
        )

    refusal = occurrence_refusal(recurrence, occurrence_start)
    if refusal is not None:
        return timeline, MaterialisationReport(refused=refusal)

    clock = _rule_clock(recurrence)
    occurrence = _occurrence(recurrence, _to_clock(occurrence_start, clock))
    if occurrence.cancelled:
        return timeline, MaterialisationReport(
            occurrence=occurrence,
            refused=(
                f"the occurrence of '{recurrence.id}' at "
                f"{occurrence.occurrence_start.isoformat()} is recorded as cancelled, "
                f"so there is nothing to put on the timeline."
            ),
        )

    existing = materialised_ids(timeline, recurrence_id).get(occurrence.occurrence_start)
    if existing is not None:
        return timeline, MaterialisationReport(
            timepoint_id=existing, created=False, occurrence=occurrence
        )

    timeline, point = add_timepoint(
        timeline,
        start=occurrence.start,
        end=occurrence.end,
        label=recurrence.label,
        recurrence_id=recurrence.id,
        occurrence_start=occurrence.occurrence_start,
    )
    return timeline, MaterialisationReport(
        timepoint_id=point.id, created=True, occurrence=occurrence
    )


def add_recurrence(
    timeline: Timeline,
    *,
    label: str,
    rule: RecurrenceRule,
    duration: timedelta = timedelta(0),
    bounds: RecurrenceBounds | None = None,
    source_id: str | None = None,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, Recurrence]:
    """Record that a source says this thing happens over and over."""
    recurrence = Recurrence(
        label=label,
        rule=rule,
        duration=duration,
        bounds=bounds or RecurrenceBounds(),
        source_id=source_id,
        judged_by=judge,
        asserted_at=at,
    )
    return (
        timeline.model_copy(update={"recurrences": [*timeline.recurrences, recurrence]}),
        recurrence,
    )


class BoundReport(BaseModel):
    """Where a rule now ends, and the history of everyone who said so."""

    recurrence_id: str | None = None
    effective_end: datetime | None = None
    bound_changes: list[BoundChange] = Field(default_factory=list)
    refused: str | None = None


def end_recurrence(
    timeline: Timeline,
    *,
    recurrence_id: str,
    ends_at: datetime | None,
    because: str,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, BoundReport]:
    """Say when a rule stopped applying, appending rather than writing over.

    **The rule is not retired.** A recurrence such as "Christmas is 24 to 26
    December, annually" never stops being true, so it has no lifecycle: what
    changes is where it ends. The most recent entry is the effective end, so "we
    thought it stopped in 1990, then learned it was 1993" is readable rather
    than overwritten.
    """
    recurrence = find_recurrence(timeline, recurrence_id)
    if recurrence is None:
        return timeline, BoundReport(
            refused=f"timeline '{timeline.id}' has no recurrence '{recurrence_id}'."
        )

    change = BoundChange(ends_at=ends_at, because=because, judged_by=judge, at=at)
    updated = recurrence.model_copy(update={"bound_changes": [*recurrence.bound_changes, change]})
    return _replace(timeline, updated), BoundReport(
        recurrence_id=recurrence_id,
        effective_end=updated.effective_end,
        bound_changes=list(updated.bound_changes),
    )


class ExceptionReport(BaseModel):
    """What became of a request to record an occurrence that broke the pattern."""

    recurrence_id: str | None = None
    exception: RecurrenceException | None = None
    refused: str | None = None


def record_exception(
    timeline: Timeline,
    *,
    recurrence_id: str,
    occurrence_start: datetime,
    kind: RecurrenceExceptionKind,
    moved_to: datetime | None = None,
    source_id: str | None = None,
    because: str | None = None,
    judge: JudgeRef | None = None,
    at: datetime,
) -> tuple[Timeline, ExceptionReport]:
    """Record that one occurrence did not happen, or happened elsewhere in time.

    The occurrence is named by the start the rule gives it, and a start the rule
    does not produce is refused: an exception against nothing would sit in the
    record looking like a cancelled service nobody can find.

    One exception per occurrence. A second one replaces the first rather than
    stacking, because "cancelled, then moved" about one occurrence is a
    correction, and two live entries would leave the reading to whoever looked.
    """
    recurrence = find_recurrence(timeline, recurrence_id)
    if recurrence is None:
        return timeline, ExceptionReport(
            refused=f"timeline '{timeline.id}' has no recurrence '{recurrence_id}'."
        )
    if kind == "moved" and moved_to is None:
        return timeline, ExceptionReport(
            recurrence_id=recurrence_id,
            refused=(
                "a moved occurrence needs `moved_to`: an occurrence that moved to "
                "nowhere is a cancellation, and the two mean different things."
            ),
        )
    refusal = occurrence_refusal(recurrence, occurrence_start)
    if refusal is not None:
        return timeline, ExceptionReport(recurrence_id=recurrence_id, refused=refusal)

    clock = _rule_clock(recurrence)
    identity = _from_clock(_to_clock(occurrence_start, clock))
    exception = RecurrenceException(
        occurrence_start=identity,
        kind=kind,
        moved_to=moved_to if kind == "moved" else None,
        source_id=source_id,
        because=because,
        judged_by=judge,
        at=at,
    )
    updated = recurrence.model_copy(
        update={
            "exceptions": [
                *(
                    existing
                    for existing in recurrence.exceptions
                    if existing.occurrence_start != identity
                ),
                exception,
            ]
        }
    )
    return _replace(timeline, updated), ExceptionReport(
        recurrence_id=recurrence_id, exception=exception
    )


def _replace(timeline: Timeline, updated: Recurrence) -> Timeline:
    return timeline.model_copy(
        update={
            "recurrences": [
                updated if recurrence.id == updated.id else recurrence
                for recurrence in timeline.recurrences
            ]
        }
    )


def preview(
    recurrence: Recurrence,
    *,
    count: int = PREVIEW_OCCURRENCES,
) -> list[Occurrence]:
    """The first few occurrences a rule produces, for a caller to check it by.

    Starts at the rule's own beginning: its `bounds.start` where it has one, its
    anchor when it is periodic, and its first occurrence when it is a calendar
    rule, which `dateutil` gives in one step. The point is to show what the rule
    was read as, so it starts where the rule does rather than near today.
    """
    clock = _rule_clock(recurrence)
    if recurrence.bounds.start is not None:
        start = _to_clock(recurrence.bounds.start, clock)
    elif isinstance(recurrence.rule, PeriodicRule):
        start = recurrence.rule.anchor
    else:
        first = next(iter(_calendar(recurrence.rule)), None)
        if first is None:
            return []
        start = first

    found: list[Occurrence] = []
    cursor: datetime | None = _after(recurrence, start, clock, inclusive=True)
    for _ in range(max(count, 0) + len(recurrence.exceptions)):
        if cursor is None or len(found) >= count:
            break
        occurrence = _occurrence(recurrence, cursor)
        if not occurrence.cancelled:
            found.append(occurrence)
        cursor = _after(recurrence, cursor, clock)
    return found


def materialised_timepoint(
    timeline: Timeline,
    *,
    recurrence_id: str,
    occurrence_start: datetime,
) -> Timepoint | None:
    """The point that materialises this occurrence, or None when nothing has."""
    point_id = materialised_ids(timeline, recurrence_id).get(occurrence_start)
    return next((point for point in timeline.timepoints if point.id == point_id), None)
