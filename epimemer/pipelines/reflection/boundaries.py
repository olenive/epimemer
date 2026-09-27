"""Reflect proposing where one claim's period ends and the next one's begins.

T1 §9's other half. Ingest extracts what a document says; **reflect proposes what
two documents say together**, and the motivating case is structurally invisible
at ingest. Document 1: *"the city is called Leningrad."* Document 2: *"the city
has been called Saint Petersburg since 1991."* The first document cannot know it
will ever stop being true, so the first fact's period is left open — and only
something seeing both can close it.

**The succession judgment is the licence, and reflect never makes it.** A
proposal is drawn from a `temporally_followed_by` edge, which is the agent's
recorded verdict that the world moved from one claim to the next (T2, §3). Given
that verdict, the interval consequence is bookkeeping rather than a second
judgment — where without it, guessing that two similar facts are successive is
exactly the judgment §3 reserves for the agent. `superseded_by` licenses nothing
here: a correction says the claim was never true, and a claim that was never true
has no period to close.

**Only a date some document actually gives is ever proposed.** The boundary is
the successor's own located start (or the predecessor's own located end), moved
across the edge — §9's *"the first interval closes before the second opens"*, as
a relation rather than a date. Publication dates are deliberately not used: a
document published in 2000 bounds when its claim was *asserted*, never when the
previous one stopped holding, and closing Leningrad's period at 2000 would have
the graph assert the city was called Leningrad in 1995. So §9's own worked
example — two documents, neither carrying any date — yields no proposal, and
that is the honest outcome. The boundary comes from a document that names a
date, read against a fact from another one.

Every proposal is `inferred` per §8 and **nothing is written here** on the
proposing side: `apply_reflection(boundaries=[...])` is what writes an accepted
one.

**A proposal can be declined, and a declined one is not offered again.** The
decline is a `boundary_declined` journal row keyed on the question itself: the
(claim, source) pair as its subjects, and the endpoint, date and clock in
`covers`. It is a row rather than an `assessed` edge because the two claims in
a succession are often also nominated as a similar or contradictory pair, and
an edge between them would silence those nominations too. A proposal whose date
later moves, because the successor's period was corrected, is a different
question and comes back. `reopen(boundary=...)` withdraws a decline.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    ValidityInterval,
    is_open_boundary,
    located,
)
from epimemer.core.types import (
    DecisionKind,
    DecisionRecord,
    EdgeType,
    EpistemicNode,
    Fact,
    Inference,
    JudgeRef,
    NodeEdge,
    NodeStatus,
)
from epimemer.pipelines.query.validity import SourceValidity, validity_for
from epimemer.pipelines.reflection.review import NodeRef
from epimemer.storage.protocol import StorageBackend

# Which statuses a claim in a succession can hold. A predecessor is normally
# `HISTORICAL` and a successor `ACTIVE`, but a recurrence reactivates the older
# one and a longer chain leaves middles retired — so both sides are drawn from
# the same set rather than from an assumption about which end is which.
SUCCESSION_STATUSES: frozenset[NodeStatus] = frozenset(
    {
        NodeStatus.ACTIVE,
        NodeStatus.HISTORICAL,
    }
)

# Only these carry validity (T1 §1). A topic is a subject rather than an
# assertion — there is nothing there to be true, so nothing to be true *during*.
_CAN_BE_TRUE = (Fact, Inference)


class BoundaryProposal(BaseModel):
    """One endpoint reflect can fill in, and everything needed to judge it.

    `current` and `proposed` are both shown rather than one plus a rule to apply
    in your head. What changes is easy to miss otherwise: the revised interval's
    basis is `inferred`, so an interval whose start a document *stated* stops
    being reportable as stated once the other end is worked out. `basis` is per
    interval rather than per endpoint (§8), which is what makes that a real cost
    rather than a presentational one — see `dev-docs/VALIDITY_DESIGN.md`.
    """

    node: NodeRef
    # The provenance edge the interval hangs off: which source's assertion is
    # being completed. Named because a claim with two sources has two periods
    # and a proposal must say which one it touches.
    source_id: str
    endpoint: str  # "start" or "end"
    at: datetime
    timeline_id: str | None = None
    current: ValidityInterval
    proposed: ValidityInterval
    # The claim on the other side of the succession edge, and the source that
    # dated it. This is the evidence, and it is a node in the graph rather than
    # a sentence in a report — the reviewer can go and read it.
    because: NodeRef
    because_source_id: str


def _revised(interval: ValidityInterval, endpoint: str, at: datetime) -> ValidityInterval:
    """The interval with one endpoint filled in, rebuilt so it is checked.

    `model_copy(update=...)` skips validation, and the validation is the point:
    a period that would start at or after it ends, or one whose own witness the
    new endpoint excludes, must raise here rather than reach storage.

    The basis becomes `inferred` because that is what this boundary is (§8) —
    worked out from two sources read together rather than copied from either.
    """
    return ValidityInterval.model_validate(
        interval.model_dump() | {endpoint: PreciseInstant(at=at), "basis": IntervalBasis.INFERRED}
    )


def _periods(sources: Sequence[SourceValidity]) -> list[tuple[str, ValidityInterval]]:
    """Every period a node holds, each paired with the source asserting it."""
    return [(source.source_id, interval) for source in sources for interval in source.intervals]


def _proposal(
    *,
    node: EpistemicNode,
    source_id: str,
    interval: ValidityInterval,
    endpoint: str,
    at: datetime,
    because: EpistemicNode,
    because_source_id: str,
) -> BoundaryProposal | None:
    """The proposal for one endpoint, or `None` where it cannot hold.

    Construction is the check: `ValidityInterval` refuses a period that starts
    at or after it ends, or one whose own witness its endpoints exclude. A
    boundary that would produce either is a boundary the evidence contradicts —
    the predecessor was still being witnessed after the successor began, say —
    and proposing it would ask the reviewer to approve something the model would
    then refuse to store.
    """
    try:
        proposed = _revised(interval, endpoint, at)
    except ValueError:
        return None

    return BoundaryProposal(
        node=NodeRef(id=node.id, content=node.content),
        source_id=source_id,
        endpoint=endpoint,
        at=at,
        timeline_id=interval.timeline_id,
        current=interval,
        proposed=proposed,
        because=NodeRef(id=because.id, content=because.content),
        because_source_id=because_source_id,
    )


def _across_one_succession(
    earlier: EpistemicNode,
    later: EpistemicNode,
    validity: Mapping[str, list[SourceValidity]],
) -> list[BoundaryProposal]:
    """Both directions of §9's relation, for one `A → B` succession.

    Closing the earlier claim is the case §9 names; opening the later one is the
    same relation read from the other end, and leaving it out would be arbitrary.

    Which date, when a side holds several periods: the **earliest** located start
    of the successor closes the predecessor, and the **latest** located end of
    the predecessor opens the successor. Both are the boundary nearest the
    handover, which is the only one the succession is evidence about.

    Periods on different clocks never meet, exactly as `compare_intervals`
    refuses to place them: there is no conversion between an in-universe date and
    a real one, and applying one would invent it.
    """
    earlier_periods = _periods(validity.get(earlier.id, []))
    later_periods = _periods(validity.get(later.id, []))

    def nearest(
        periods: Sequence[tuple[str, ValidityInterval]],
        timeline_id: str | None,
        endpoint: str,
        pick,
    ) -> tuple[str, datetime] | None:
        dated = [
            (source_id, moment)
            for source_id, moment in (
                (source_id, located(getattr(interval, endpoint)))
                for source_id, interval in periods
                if interval.timeline_id == timeline_id
            )
            if moment is not None
        ]
        return pick(dated, key=lambda pair: pair[1]) if dated else None

    proposals: list[BoundaryProposal] = []

    for source_id, interval in earlier_periods:
        if not is_open_boundary(interval.end):
            continue
        opens = nearest(later_periods, interval.timeline_id, "start", min)
        if opens is None:
            continue
        proposal = _proposal(
            node=earlier,
            source_id=source_id,
            interval=interval,
            endpoint="end",
            at=opens[1],
            because=later,
            because_source_id=opens[0],
        )
        if proposal is not None:
            proposals.append(proposal)

    for source_id, interval in later_periods:
        if not is_open_boundary(interval.start):
            continue
        closes = nearest(earlier_periods, interval.timeline_id, "end", max)
        if closes is None:
            continue
        proposal = _proposal(
            node=later,
            source_id=source_id,
            interval=interval,
            endpoint="start",
            at=closes[1],
            because=earlier,
            because_source_id=closes[0],
        )
        if proposal is not None:
            proposals.append(proposal)

    return proposals


def succession_holders(nodes: Iterable[EpistemicNode]) -> dict[str, EpistemicNode]:
    """The nodes among these that a succession can be about, keyed by id.

    A topic is a subject rather than an assertion, so there is nothing there to
    be true and nothing to be true *during*; a claim outside
    `SUCCESSION_STATUSES` has been merged away or concluded wrong, and neither
    is something to align a period to.
    """
    return {
        node.id: node
        for node in nodes
        if isinstance(node, _CAN_BE_TRUE) and node.status in SUCCESSION_STATUSES
    }


# One declined proposal: the claim, the source whose period it would change,
# and the question as `boundary_question` writes it.
DeclinedBoundary = tuple[str, str, str]


def boundary_question(endpoint: str, at: datetime, timeline_id: str | None) -> str:
    """The question a boundary proposal asks, as one string.

    Written into a `boundary_declined` row's `covers` and read back by every
    nominator, so the format lives here and nowhere else. The (claim, source)
    pair is the row's subjects; this is the rest of what makes one proposal
    that proposal: which end, at what moment, on which clock.

    The moment is normalised to UTC before it is written, and a date with no
    zone is read as UTC, as `PreciseInstant` reads one. A decline copied from
    the JSON `reflect` returned then matches the date the nominator computes,
    whatever zone it was rendered in. An empty clock is the real calendar, as
    `None` is.
    """
    moment = (at if at.tzinfo is not None else at.replace(tzinfo=UTC)).astimezone(UTC)
    clock = f" on timeline {timeline_id}" if timeline_id else ""
    return f"{endpoint} at {moment.isoformat()}{clock}"


def declined_boundaries_from(records: Iterable[DecisionRecord]) -> frozenset[DeclinedBoundary]:
    """The declines still standing, replayed from journal rows.

    Pure, so the nominator and the visualization snapshot read one rule. Rows
    are replayed oldest first whatever order they arrive in, as
    `confirmed_reasons_for` does for a keep: a `boundary_declined` row adds its
    question, a `reopened` row with the same two subjects and the same key takes
    it away, and a decline written after that stands again.

    A `reopened` row with no key is a fact pair's, which names no question, so
    it withdraws nothing here. Rows of any other kind are ignored.
    """
    declined: set[DeclinedBoundary] = set()
    for record in sorted(records, key=lambda row: (row.decided_at, row.id)):
        if len(record.subject_ids) != 2:
            continue
        node_id, source_id = record.subject_ids
        keys = {(node_id, source_id, question) for question in record.covers}
        if record.kind is DecisionKind.BOUNDARY_DECLINED:
            declined |= keys
        elif record.kind is DecisionKind.REOPENED:
            declined -= keys
    return frozenset(declined)


async def declined_boundaries_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> frozenset[DeclinedBoundary]:
    """The standing declines about these claims, in one batched query."""
    ids = list(node_ids)
    if not ids:
        return frozenset()
    rows = await storage.query_decisions(
        kinds=[DecisionKind.BOUNDARY_DECLINED, DecisionKind.REOPENED], subject_ids=ids
    )
    return declined_boundaries_from(rows)


def boundary_proposals_from(
    holders: Mapping[str, EpistemicNode],
    successions: Sequence[NodeEdge],
    validity: Mapping[str, list[SourceValidity]],
    declined: frozenset[DeclinedBoundary] = frozenset(),
) -> list[BoundaryProposal]:
    """The rule itself, over data somebody has already read.

    Pure, and the only place the rule is written. `reflect` reaches it through
    `propose_boundaries`, which does the reading; a visualization snapshot
    reaches it from the nodes and edges it has already loaded, so the strips it
    draws and the proposals beside them describe one instant rather than two.

    `successions` may be any edges at all: the succession edges are picked out
    here, and a step whose other end is not a holder is dropped, since that end
    is a claim the graph no longer carries.

    `declined` holds the proposals a judge has declined, from
    `declined_boundaries_from`. A proposal is left out only when its claim,
    source and question all match, so a decline about another date or the
    other endpoint suppresses nothing.
    """
    pairs = [
        (holders[edge.src_id], holders[edge.dst_id])
        for edge in successions
        if edge.type is EdgeType.TEMPORALLY_FOLLOWED_BY
        and edge.src_id in holders
        and edge.dst_id in holders
    ]
    return [
        proposal
        for earlier, later in pairs
        for proposal in _across_one_succession(earlier, later, validity)
        if (
            proposal.node.id,
            proposal.source_id,
            boundary_question(proposal.endpoint, proposal.at, proposal.timeline_id),
        )
        not in declined
    ]


async def propose_boundaries(
    storage: StorageBackend,
) -> list[BoundaryProposal]:
    """Where a succession lets one claim's period close and the next one's open.

    Reads only, in five batched queries: the claims on both sides of a
    succession, their lineage edges, their validity, and the declines standing
    against them. The rule those reads feed is `boundary_proposals_from`.

    A proposal needs a succession edge *and* a date, so a graph with either and
    not the other produces nothing. That is the common case and stays the common
    case: this is sparse by design, like everything else validity touches.
    """
    holders: dict[str, EpistemicNode] = {}
    for status in sorted(SUCCESSION_STATUSES, key=lambda s: s.value):
        holders.update(succession_holders(await storage.query_nodes(status=status)))
    if not holders:
        return []

    by_node = await storage.get_edges_for(
        list(holders), direction="from", edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY
    )
    successions = [edge for edges in by_node.values() for edge in edges]
    pairs = [
        (edge.src_id, edge.dst_id)
        for edge in successions
        if {edge.src_id, edge.dst_id} <= holders.keys()
    ]
    if not pairs:
        return []

    node_ids = list(dict.fromkeys(node_id for pair in pairs for node_id in pair))
    validity = await validity_for(node_ids, storage)
    declined = await declined_boundaries_for(node_ids, storage)
    return boundary_proposals_from(holders, successions, validity, declined)


class BoundaryRefused(BaseModel):
    """Why one requested boundary was not written."""

    node_id: str
    reason: str


async def apply_boundary(
    storage: StorageBackend,
    *,
    node_id: str,
    source_id: str,
    endpoint: str,
    at: datetime,
    timeline_id: str | None = None,
) -> BoundaryRefused | None:
    """Write one accepted boundary, or say why it was not written.

    Re-derives which interval is meant from the graph as it stands rather than
    trusting a proposal that may be stale, and **requires exactly one candidate**
    — the interval on that source's edge, on that clock, still open at that
    endpoint. Several means the request is ambiguous and none means it has
    already been answered; both refuse rather than guess, because the thing being
    overwritten is what a source is recorded as asserting.

    The written interval's basis becomes `inferred` (§8). That is a real loss
    where the other endpoint was `stated`, and it is the price of `basis` being
    per interval; the alternative — leaving it `stated` — would have a source
    appear to assert a date no document gave, which is the one thing §8 exists to
    prevent.
    """
    if endpoint not in ("start", "end"):
        return BoundaryRefused(node_id=node_id, reason=f"'{endpoint}' is not an endpoint")

    node = await storage.get_node(node_id)
    if node is None or node.status not in SUCCESSION_STATUSES:
        return BoundaryRefused(
            node_id=node_id,
            reason="no such claim, or not one a succession can be about",
        )

    edges = [
        edge
        for edge in await storage.get_edges_from(node_id, edge_type=EdgeType.SOURCED_FROM)
        if edge.dst_id == source_id
    ]
    if len(edges) != 1:
        return BoundaryRefused(
            node_id=node_id,
            reason=f"{len(edges)} provenance edges name source '{source_id}'",
        )
    edge = edges[0]

    candidates = [
        index
        for index, interval in enumerate(edge.validity)
        if interval.timeline_id == timeline_id and is_open_boundary(getattr(interval, endpoint))
    ]
    if len(candidates) != 1:
        return BoundaryRefused(
            node_id=node_id,
            reason=(
                f"{len(candidates)} periods from that source are still open at their {endpoint}"
            ),
        )

    index = candidates[0]
    try:
        revised = _revised(edge.validity[index], endpoint, at)
    except ValueError as problem:
        return BoundaryRefused(node_id=node_id, reason=str(problem))

    validity = list(edge.validity)
    validity[index] = revised
    await storage.store_edge(_with_validity(edge, validity))
    return None


async def record_boundary_decline(
    storage: StorageBackend,
    *,
    node_id: str,
    source_id: str,
    endpoint: str,
    at: datetime,
    timeline_id: str | None = None,
    because: str,
    judge: JudgeRef | None = None,
) -> BoundaryRefused | None:
    """Write one declined boundary proposal, or say why it was not written.

    One `boundary_declined` journal row: subjects `[node_id, source_id]`, as the
    accepting `boundary` row has them, `covers` the question from
    `boundary_question`, and `because` as `certainty_basis`. A decline is a
    judgment against evidence the graph shows, and the next reader is owed why.
    Nothing on the graph changes. An `assessed` edge would silence the pair's
    similarity and contradiction nominations as well, which a decline about a
    date has no business doing.

    Checked against the graph as cheaply as `apply_boundary`'s first two
    refusals: the endpoint is one, the claim exists and can hold a period, and
    exactly one provenance edge names that source. The proposal need not be on
    offer at this moment, so a decline made from a snapshot still lands. A
    question already declined and not reopened is refused rather than written
    twice.

    **A failed write raises**, as `record_retention` does: the row is the
    decline, so a decline reported as recorded whose row was lost would put the
    proposal back on every reflect in front of a judge told it was answered.
    """
    if endpoint not in ("start", "end"):
        return BoundaryRefused(node_id=node_id, reason=f"'{endpoint}' is not an endpoint")

    node = await storage.get_node(node_id)
    if node is None or node.status not in SUCCESSION_STATUSES:
        return BoundaryRefused(
            node_id=node_id,
            reason="no such claim, or not one a succession can be about",
        )

    edges = [
        edge
        for edge in await storage.get_edges_from(node_id, edge_type=EdgeType.SOURCED_FROM)
        if edge.dst_id == source_id
    ]
    if len(edges) != 1:
        return BoundaryRefused(
            node_id=node_id,
            reason=f"{len(edges)} provenance edges name source '{source_id}'",
        )

    question = boundary_question(endpoint, at, timeline_id)
    if (node_id, source_id, question) in await declined_boundaries_for([node_id], storage):
        return BoundaryRefused(
            node_id=node_id,
            reason=(
                f"already declined: '{question}' on source '{source_id}' has a "
                f"standing decline, and `reopen(boundary=...)` is what withdraws one"
            ),
        )

    await storage.record_decision(
        DecisionRecord(
            kind=DecisionKind.BOUNDARY_DECLINED,
            subject_ids=[node_id, source_id],
            covers=[question],
            judged_by=judge,
            certainty_basis=because,
        )
    )
    return None


def _with_validity(edge: NodeEdge, validity: list[ValidityInterval]) -> NodeEdge:
    """The same edge, same id, carrying a different set of periods.

    Rebound rather than mutated in place: `model_copy` shares the list, so
    appending to it would reach back into whatever the backend handed over.
    """
    return edge.model_copy(update={"validity": validity})


# --- Correcting an interval that is present and wrong


class IntervalCorrectionRefused(BaseModel):
    """Why one interval correction was not made. Prose, as `BoundaryRefused` is."""

    node_id: str
    source_id: str
    reason: str


class IntervalCorrected(BaseModel):
    """One corrected set of periods, and what it replaced.

    `was` is the whole prior list rather than a diff, because an interval has no
    identity of its own — it is a position in a list on one edge — so there is
    nothing a diff could be keyed on.
    """

    node_id: str
    source_id: str
    was: list[ValidityInterval]
    now: list[ValidityInterval]


async def correct_interval(
    storage: StorageBackend,
    *,
    node_id: str,
    source_id: str,
    intervals: Sequence[ValidityInterval],
    because: str,
    judge: JudgeRef | None = None,
) -> IntervalCorrectionRefused | IntervalCorrected:
    """Replace what one source is recorded as asserting about when a claim held.

    **This lives here rather than in `rejudge` because of how it is addressed.**
    `rejudge` names a node; an interval belongs to a **(node, source) pair**, so
    folding it in would grow a `source_id` parameter that applies to exactly one
    of that tool's fields — this repo's own tell that two tools are wearing one
    name. It lives beside `apply_boundary` because that is the other code that
    reasons about endpoints on this edge.

    **And it is not `apply_boundary`.** That fills an endpoint which is *open*,
    where a succession implies one, and it is the half that can ever be
    automated. This overwrites an endpoint that is **present and wrong**, which
    no proposal can derive — the graph has no way to know a stated date was
    misread. Different evidence, different act, so two calls rather than a flag.

    **The whole list is replaced.** An interval is a position in a list on one
    edge and has no id, so there is nothing to address one by. Supplying an
    empty list is allowed and is the correction for an interval that was
    invented outright: refusing it would leave a fabricated period unremovable,
    which is the same revisability defect a second time.

    **The prior list is kept** in the edge's `metadata["interval_corrections"]`.
    Corroboration reads intervals to decide whether a look-alike is a
    witness or an adjacent period, so a wrong interval has been silently moving
    counts for as long as it stood; the trail plus the journal row's timestamp is
    what bounds which answers were affected.

    `basis` is the caller's to state per interval and is not forced to
    `inferred` here, unlike `apply_boundary`: a correction is often restoring
    what the document actually said, and calling that inferred would understate
    it.
    """
    if not because.strip():
        return IntervalCorrectionRefused(
            node_id=node_id,
            source_id=source_id,
            reason=(
                "`because` is required: this overwrites what a source is "
                "recorded as asserting, which corroboration and `graph_as_of` "
                "both read. The graph has to carry why."
            ),
        )

    node = await storage.get_node(node_id)
    if node is None:
        return IntervalCorrectionRefused(
            node_id=node_id, source_id=source_id, reason=f"no such node: {node_id}."
        )

    edges = [
        edge
        for edge in await storage.get_edges_from(node_id, edge_type=EdgeType.SOURCED_FROM)
        if edge.dst_id == source_id
    ]
    if len(edges) != 1:
        return IntervalCorrectionRefused(
            node_id=node_id,
            source_id=source_id,
            reason=(
                f"{len(edges)} provenance edges name source '{source_id}'. "
                f"Exactly one is needed, because the periods being replaced "
                f"belong to one edge and there is no way to say which."
            ),
        )
    edge = edges[0]

    replacement = list(intervals)
    if replacement == list(edge.validity):
        return IntervalCorrectionRefused(
            node_id=node_id,
            source_id=source_id,
            reason=(
                "every period supplied is what the edge already carries, so "
                "there is nothing to revise."
            ),
        )

    was = list(edge.validity)
    revised = _with_validity(edge, replacement)
    revised.metadata = {
        **edge.metadata,
        # Append-only, and the only place the prior periods survive.
        "interval_corrections": [
            *edge.metadata.get("interval_corrections", []),
            {
                "because": because,
                "was": [interval.model_dump(mode="json") for interval in was],
                "now": [interval.model_dump(mode="json") for interval in replacement],
                "judged_by": judge.model_dump(mode="json") if judge else None,
            },
        ],
    }
    await storage.store_edge(revised)
    return IntervalCorrected(node_id=node_id, source_id=source_id, was=was, now=replacement)
