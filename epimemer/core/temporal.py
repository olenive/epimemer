"""Valid time — imprecise instants, validity intervals, and their comparison.

This module is about when a claim was **true**. That is *valid time*, and it is
a different axis from *transaction time* — `created_at`, `superseded_at` and
`graph_as_of`, which record what the graph held and when. The two blur silently
once there is code and are near-impossible to separate afterwards, so the
validity model
fixed the vocabulary before the fields existed and this module keeps to it.

Everything here is pure: no clock is read, no storage is touched, no network
call is made, so every answer is a function of the arguments. Where validity
*lives* — one list per `sourced_from` edge, so a period is always attributable
to the source that asserted it — is a separate step and not this module's
business.

**No collapse is ever applied by default, and none of it produces a new
interval.** Union takes one careful source and one sloppy one and yields a
period neither claims; intersection turns two different episodes into "never"
(T1 §3). Nothing here does either. What it does offer is two *named questions* a
caller can ask of a set — `validity_at` and `assertions_are_disjoint` — each
stating the rule it applies and each answering with a verdict rather than an
interval. Asking one is a caller supplying its own rule, which is what §3 asks
of it; the rules live here so that each is written once and tested once rather
than re-derived at every call site.
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# --- Endpoints ---
#
# Three states, four classes. The states are T1 §4's: a boundary that is
# located, a boundary that exists but is not located, and no boundary at all.
#
# `unknown` and `unbounded` being different values is the load-bearing part.
# *"The city is named Placeberg"* has an unknown start — it may or may not
# always have been so, and assuming either is a fabrication. *"Water is H₂O"*
# has no start. Collapsing the two was the first draft of this design and it
# reproduced the empty-set ambiguity one level down. It is also why `Timepoint`
# is not reused: its `start: datetime | None` means both, and it models *mention*
# time (dates named in content), which is a different thing from *true during*.
# One fact routinely has both — *"the 1991 renaming"* mentions 1991, while *"the
# city is called Leningrad"* is true until 1991.
#
# The fourth class splits §4's single "point (concrete instant or free-text
# label)" state in two, because the alternative is `at: datetime | None` with a
# label beside it — the same `None`-means-two-things shape §4 rejects
# `Timepoint` for. A named endpoint is a point the source gave in words; it
# compares exactly as `unknown` does and differs only in carrying the words.


class PreciseInstant(BaseModel):
    """A boundary located on the timeline.

    `label` keeps the source's own words when a `NamedInstant` is resolved:
    the phrase is the evidence for the date, and dropping it on resolution
    would leave a bare datetime nobody can audit.
    """

    instant_kind: Literal["precise"] = "precise"
    at: datetime
    label: str | None = None

    @field_validator("at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        """Naive datetimes are read as UTC, once, here.

        A hand-written historical date is naive far more often than not, and
        comparing a naive datetime to an aware one raises rather than answering
        — which would turn a data-entry detail into an exception thrown from the
        middle of a comparison. UTC is a guess, but it is the same guess `_now()`
        already makes, and making it at construction beats making it in every
        consumer.
        """
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class NamedInstant(BaseModel):
    """A boundary the source named but did not locate — "during the Renaissance".

    Stored and never silently resolved. A label is what the source said; a bound
    is what can be computed, and turning one into the other is an act with a
    justification, not a parsing convenience. So it contributes *unknown* to
    every comparison until somebody resolves it explicitly into a
    `PreciseInstant` carrying the label forward. Same split as content versus
    embedding.
    """

    instant_kind: Literal["named"] = "named"
    label: str


class UnknownInstant(BaseModel):
    """There is a boundary. Where it falls is not known."""

    instant_kind: Literal["unknown"] = "unknown"


class UnboundedInstant(BaseModel):
    """There is no boundary. *Water is H₂O* has no start."""

    instant_kind: Literal["unbounded"] = "unbounded"


# A moment that may be imprecise. Named for the general shape rather than for
# validity, because `RawDocument.published_at` will use it too and a document
# should not have to depend on something called validity-anything.
#
# A discriminated union of models rather than a class hierarchy (CLAUDE.md), and
# the extensibility rule is discipline rather than schema: **no consumer
# branches on `instant_kind`.** Everything downstream asks the comparison
# question and consumes the answer, so adding a kind — a probability
# distribution, if that day comes — touches this module and nothing else. A test
# pins that.
ImpreciseInstant = Annotated[
    PreciseInstant | NamedInstant | UnknownInstant | UnboundedInstant,
    Field(discriminator="instant_kind"),
]


class IntervalBasis(str, Enum):
    """How the interval was arrived at (T1 §8).

    The agent is not a source: a hallucinated interval is indistinguishable from
    a documented one once stored. But the line between copying and inventing is
    not clean — judging *tense*, whether *"the city is called X"* leaves an
    interval open, is already reading rather than copying — so every interval
    says which it was, and a caller can filter to stated-only. Without the
    marking, "stick to the source" is a prompt instruction with nothing checking
    it.

    World knowledge deliberately has no member here. It is forbidden, not
    marked: a value for it would make it storable.
    """

    STATED = "stated"      # dates present in the text
    INFERRED = "inferred"  # from tense, context, or two sources read together


class ValidityInterval(BaseModel):
    """A period one source asserts a claim was true, on one clock.

    **Open world.** The interval says what a source asserts and asserts nothing
    about the outside, so a moment outside every stated interval is *unknown*,
    not false. Closed-world assertions ("Labour governed *only* during…") would
    need their own marking and none is proposed.

    **Half-open, `[start, end)`.** T1 leaves this open and it has to be decided
    somewhere: an instant belongs to the period that starts on it, not to the
    one that ends there. Otherwise the exact moment of the 1991 renaming is one
    the city is provably called both names, and every adjacent pair of periods
    overlaps by a point.

    `timeline_id` is the clock, not the frame — the two axes cross both ways.
    One frame can need two clocks (a revision to a fictional history has a real
    publication date *and* in-universe dates) and two frames can share one
    (competing accounts of real history both run on CE dates). `None` is the
    default wall-clock timeline, which is what real-world facts use, so the
    common case needs no decision from any caller. Only the clock is a
    reference: a missing timeline leaves the interval fully readable and merely
    incomparable.
    """

    # Unknown is the honest default for an endpoint nobody supplied. Defaulting
    # to unbounded would have every under-specified interval claim the claim was
    # always true.
    start: ImpreciseInstant = Field(default_factory=UnknownInstant)
    end: ImpreciseInstant = Field(default_factory=UnknownInstant)
    timeline_id: str | None = None
    # "This interval is asserted to contain T." Not redundant with a document's
    # publication date, and they come apart in both directions: an undated
    # document saying "as of March 1990…" has a witness and no publication date;
    # a diary published in 1990 recounting 1985 in the present tense has both,
    # differing. With three endpoint states there is no other way to say
    # "contains 1990" — that is a bound, and bounds are what T1 chose not to
    # model — so without this two undated facts can never provably overlap and
    # the soundness check never fires. One witness per interval: a source
    # asserting a claim at two separated moments writes two intervals.
    witnessed_at: ImpreciseInstant | None = None
    # No default, deliberately, on `SUPERSESSION_REASONS`' grounds: a default
    # here would silently mark inferred boundaries as stated, which is the one
    # error this field exists to make impossible.
    basis: IntervalBasis

    @model_validator(mode="after")
    def _reject_self_contradiction(self) -> "ValidityInterval":
        problem = interval_inconsistency(self)
        if problem is not None:
            raise ValueError(problem)
        return self


def interval_inconsistency(interval: ValidityInterval) -> str | None:
    """Why this interval contradicts itself, or `None` if it does not.

    Fires only on a *definite* contradiction — both offending positions located
    — so an interval full of unknowns is always accepted. That keeps this from
    becoming a check on unknown, which §11 rules out for the soundness check on
    the same grounds.

    An interval that starts after it ends, or that claims to have been witnessed
    at a moment its own endpoints exclude, is a construction error rather than a
    source disagreeing with itself: no document says *"as of 1990, Labour
    governed 1997–2010"*. Left unchecked it would also let the comparison below
    derive an overlap from a premise that cannot hold.
    """
    start, end = _start_position(interval.start), _end_position(interval.end)
    if start is not None and end is not None and start >= end:
        return (
            f"a validity interval must start before it ends, and this one runs "
            f"{start.isoformat()} → {end.isoformat()}. Equal endpoints are "
            f"empty under half-open semantics and assert nothing; a claim true "
            f"at a single moment is a witness point, not a zero-width interval."
        )

    witness = located(interval.witnessed_at)
    outside = witness is not None and (
        (start is not None and witness < start) or (end is not None and witness >= end)
    )
    if outside:
        return (
            f"a witness point says the interval contains it, but "
            f"{witness.isoformat()} falls outside this interval's own endpoints."
        )
    return None


def merged_validity(
    kept: Sequence[ValidityInterval], folded: Sequence[ValidityInterval]
) -> list[ValidityInterval]:
    """One source's periods, when two edges to that source collapse into one.

    Merging two nodes leaves one provenance edge per document, and the edge that
    loses the collision must not take its intervals with it. The field is a list
    precisely because one source can assert several disjoint periods, so keeping
    both is what the model already means — and "intervals survive a merge for
    free" is the property that put validity on the edge in the first place.

    **This is not the union T1 §3 forbids.** That union is across *sources*,
    where it takes one careful source and one sloppy one and yields a period
    neither claims. Both lists here came from the same document about what is
    now the same claim, so no source's assertion is being widened by another's.

    Exact duplicates are dropped: one source asserting the same period twice is
    one assertion, and a repeated entry would later read as two.
    """
    merged = list(kept)
    for interval in folded:
        if interval not in merged:
            merged.append(interval)
    return merged


class TemporalRelation(str, Enum):
    """How two intervals sit relative to each other (T1 §10).

    A deliberate simplification of Allen's interval algebra, whose thirteen
    relations are all derivable from the endpoints on demand — so nothing is
    lost by not storing them, and four are enough for the checks that need this.

    `unknown` is its own value and never a probability. *No information about
    the ordering* and *genuinely even odds* are different claims, and collapsing
    them is the defect review caught on `relevance`, `novelty`,
    `confidence` and the empty validity set. If distributions ever land, a
    probability rides on the three known answers and never stands in for this
    one.
    """

    BEFORE = "before"
    AFTER = "after"
    OVERLAP = "overlap"
    UNKNOWN = "unknown"


def compare_intervals(a: ValidityInterval, b: ValidityInterval) -> TemporalRelation:
    """Where `a` sits relative to `b`, answering `unknown` unless it is certain.

    Every answer other than `unknown` is a claim that the two intervals *cannot*
    sit any other way given what their endpoints say. Anything an unknown or
    merely-named endpoint leaves open comes back `unknown`, which is the common
    outcome and is meant to be: this feature is sparse by design, and a model
    built to admit ignorance must propagate that ignorance rather than round it
    away.

    Two intervals on different clocks are `unknown`, never disjoint — there is
    no conversion between an in-universe date and a real one, and answering
    `before` would invent one. Useful side-effect: an inference drawn across an
    in-universe fact and a real-world fact is temporally uncheckable, which is
    the temporal sibling of `cross-frame` and worth surfacing on its own.
    """
    if a.timeline_id != b.timeline_id:
        return TemporalRelation.UNKNOWN
    if _definitely_overlap(a, b):
        return TemporalRelation.OVERLAP
    if _reaches_no_later_than(_end_position(a.end), _start_position(b.start)):
        return TemporalRelation.BEFORE
    if _reaches_no_later_than(_end_position(b.end), _start_position(a.start)):
        return TemporalRelation.AFTER
    return TemporalRelation.UNKNOWN


class ValidityVerdict(str, Enum):
    """What a set of intervals says about one moment (T3's retrieval buckets).

    **There is no third member, and its absence is the design.** T3 named three
    buckets — provably valid, unknown, and *excluded: provably not valid* — but
    T1 §6 settled the semantics afterwards and governs: an interval says what a
    source **asserts** and asserts nothing about the outside, so a moment outside
    every stated interval is *unknown*, not false. Nothing in the model can prove
    a claim was not true at a moment; only a closed-world marking
    ("Labour governed *only* during…") could, and §6 proposes none.

    So the excluded bucket is unreachable rather than merely empty, and a value
    that can never be produced is worse than no value at all — a caller would
    write a branch for it and the branch would be dead. If closed-world
    assertions ever land, the member lands with them.

    This sharpens T3's argument rather than weakening it. A valid-time *filter*
    was rejected there for turning missing metadata into a silent false negative;
    under §6 it is not merely dishonest but unimplementable, because there is no
    negative to filter on.
    """

    VALID = "valid"
    UNKNOWN = "unknown"


def validity_at(
    intervals: Sequence[ValidityInterval],
    moment: datetime,
    *,
    timeline_id: str | None = None,
) -> ValidityVerdict:
    """Whether some interval provably has `moment` inside it, on one clock.

    **This is a collapse across sources, and it is the caller's rule rather than
    a default** (T1 §3). Retrieval is the caller here and T3 is the rule: *does
    any source assert this claim held at `moment`?* The per-source intervals stay
    visible beside the answer, so nothing is hidden by it — which is the whole
    condition under which §3 permits a collapse at all. Existential rather than
    universal on purpose: two sources describing two different episodes both
    answer the question, and an intersection would call that "never".

    Intervals on another clock contribute nothing, exactly as `compare_intervals`
    answers `unknown` across clocks: there is no conversion between an in-universe
    date and a real one, and applying one would invent it. `timeline_id=None` is
    the default wall clock, which is what real-world facts use.

    A node with no intervals is `unknown`, which is the common case and is meant
    to be. Naive moments are read as UTC, on `PreciseInstant._assume_utc`'s
    grounds.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (
        ValidityVerdict.VALID
        if any(
            _definitely_holds_at(interval, moment)
            for interval in intervals
            if interval.timeline_id == timeline_id
        )
        else ValidityVerdict.UNKNOWN
    )


def assertions_are_disjoint(
    a: Sequence[ValidityInterval], b: Sequence[ValidityInterval]
) -> bool:
    """Whether every period asserted of `a` provably falls clear of every one of `b`.

    The rule behind T1 §11's soundness check, and the reason it is written here
    rather than at the call site: the wording is the whole of the guarantee, and
    two implementations of it would not agree.

    **It does not say the two were never both true.** §6 settles that nothing
    can: an interval says what a source asserts and asserts nothing about the
    outside, so no arrangement of intervals rules out a moment neither of them
    mentions. What this says is the honest, narrower thing — *no source asserts
    them true at a common moment* — and that is exactly the claim an unsoundness
    flag should make.

    **The collapse per side is the existential union**, the set of moments *some*
    source asserts the claim held (§11, second pass). It is the one collapse §3
    permits, and only because its error direction is safe: a sloppy, over-wide
    source can *suppress* a flag and never manufacture one. Reaching for the
    intersection instead produces false flags, which is the dangerous direction
    and the reason the rule is spelled out.

    **A pair that is merely `unknown` blocks the answer**, rather than counting
    as disjoint. Every cross pair must compare `before` or `after`: one that
    cannot be placed might well overlap, and firing on it would make this a check
    on ignorance — which §11 rules out and which is what a sparse feature would
    turn into if the default went the other way. An empty side answers `False`
    for the same reason: a claim nobody dated is not evidence of anything, and
    intervals on different clocks compare `unknown` and so never fire either.
    """
    if not a or not b:
        return False
    return all(
        compare_intervals(one, other)
        in (TemporalRelation.BEFORE, TemporalRelation.AFTER)
        for one in a
        for other in b
    )


# --- Positions on the line ---
#
# The only place `instant_kind` is read. An endpoint becomes either a datetime
# to compare or `None` for "says nothing", and unboundedness is resolved by
# which end it sits on — an unbounded start is the beginning of time, an
# unbounded end is the end of it. Sentinels rather than infinities because
# datetimes and floats do not compare, and no arithmetic is ever done on them.

_BEGINNING_OF_TIME = datetime.min.replace(tzinfo=timezone.utc)
_END_OF_TIME = datetime.max.replace(tzinfo=timezone.utc)


def _start_position(instant: ImpreciseInstant) -> datetime | None:
    if instant.instant_kind == "precise":
        return instant.at
    if instant.instant_kind == "unbounded":
        return _BEGINNING_OF_TIME
    return None


def _end_position(instant: ImpreciseInstant) -> datetime | None:
    if instant.instant_kind == "precise":
        return instant.at
    if instant.instant_kind == "unbounded":
        return _END_OF_TIME
    return None


def located(instant: ImpreciseInstant | None) -> datetime | None:
    """The moment an instant names, if it names one — otherwise `None`.

    Public because it is the question §4's rule expects consumers to ask instead
    of reading the kind: *did a source put a date here?* `unbounded` answers
    `None` rather than a sentinel, which is right for both callers. A witness is
    a point, so unboundedness is meaningless there; and an endpoint that is
    unbounded is a source saying there is no boundary, not a date it gave.
    """
    if instant is None or instant.instant_kind != "precise":
        return None
    return instant.at


def is_open_boundary(instant: ImpreciseInstant) -> bool:
    """Whether this endpoint is one nothing has been said about at all.

    The question a caller means by "can I fill this in": there is a boundary,
    nobody located it, and no source gave words for it. `named` is excluded
    because a label is what a source said and resolving one into a date is an
    explicit act with a justification (§4), never something a sweep does
    quietly; `unbounded` is excluded because a source saying there is no
    boundary is not a gap to close.

    It lives here rather than at the call site for §4's reason: the kind is read
    in this module and nowhere else, so a fifth kind — a distribution, if that
    day comes — decides how it answers here and touches nothing downstream.
    """
    return instant.instant_kind == "unknown"


def _strictly_earlier(earlier: datetime | None, later: datetime | None) -> bool:
    """Whether `earlier` provably falls before `later`, `None` meaning no answer.

    A sentinel settles the comparison even when the other side says nothing:
    every moment of a non-empty interval falls after the beginning of time and
    before the end of it. That is what lets a period nobody has dated overlap a
    claim asserted to have always held — which is true, and a rule demanding
    both sides be located would miss it.

    Identity, not equality: a source that genuinely names `datetime.min` is a
    located endpoint and must not be read as unboundedness.
    """
    if earlier is later:
        return False
    if earlier is _BEGINNING_OF_TIME or later is _END_OF_TIME:
        return True
    if earlier is _END_OF_TIME or later is _BEGINNING_OF_TIME:
        return False
    return earlier is not None and later is not None and earlier < later


def _reaches_no_later_than(earlier: datetime | None, later: datetime | None) -> bool:
    if earlier is _BEGINNING_OF_TIME or later is _END_OF_TIME:
        return True
    if earlier is _END_OF_TIME or later is _BEGINNING_OF_TIME:
        return False
    return earlier is not None and later is not None and earlier <= later


def _definitely_contains(interval: ValidityInterval, moment: datetime) -> bool:
    """Whether the interval's own endpoints put `moment` inside it, half-open."""
    return _reaches_no_later_than(
        _start_position(interval.start), moment
    ) and _strictly_earlier(moment, _end_position(interval.end))


def _definitely_holds_at(interval: ValidityInterval, moment: datetime) -> bool:
    """Whether the interval provably contains `moment`, its witness included.

    `_definitely_contains` above asks the endpoints alone, which is what the
    overlap rule wants of *another* interval's witness. Asked about a moment of
    the caller's choosing, an interval's **own** witness answers where an
    endpoint cannot — and it has to, or the commonest shape of a current claim
    is unreadable. *"Called Saint Petersburg since 1991"* has a located start
    and an **unknown** end (the claim may stop tomorrow; `unbounded` would say it
    never can), so its endpoints alone conclude nothing about 2010. A witness in
    2020 does: the period was still running then, so 2010 is inside it.

    A witness reaches from itself back to the start and forward to itself, and no
    further. The same interval says nothing about 2021 — the end is still
    unknown, and this is the reach of an inside bound rather than a new endpoint.
    """
    witness = located(interval.witnessed_at)
    at_or_after_start = _reaches_no_later_than(
        _start_position(interval.start), moment
    ) or (witness is not None and witness <= moment)
    before_end = _strictly_earlier(moment, _end_position(interval.end)) or (
        witness is not None and moment <= witness
    )
    return at_or_after_start and before_end


def _definitely_overlap(a: ValidityInterval, b: ValidityInterval) -> bool:
    """Whether some moment is provably inside both.

    Endpoints answer it when they are located enough to. Witness points answer
    it when they are not: two facts whose periods are entirely unknown, each
    asserted as of the same moment, provably held together at that moment —
    which is the whole reason witness points exist, since the soundness check
    would otherwise never fire on undated sources.

    Witnesses can only ever *add* an overlap, never an ordering: a witness bounds
    one endpoint from the inside, and an inside bound can show a period reaches a
    moment but never that it stops before one.
    """
    if _strictly_earlier(
        _start_position(a.start), _end_position(b.end)
    ) and _strictly_earlier(_start_position(b.start), _end_position(a.end)):
        return True

    witness_a, witness_b = located(a.witnessed_at), located(b.witnessed_at)
    if witness_a is not None and witness_a == witness_b:
        return True
    if witness_a is not None and _definitely_contains(b, witness_a):
        return True
    if witness_b is not None and _definitely_contains(a, witness_b):
        return True
    return False
