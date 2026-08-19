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

Every proposal is `inferred` per §8 and **nothing is written here**: this module
reads, and `apply_reflection(boundaries=[...])` is the only thing that writes.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel

from epimemer.core.temporal import (
    IntervalBasis,
    PreciseInstant,
    ValidityInterval,
    is_open_boundary,
    located,
)
from epimemer.core.types import (
    EdgeType,
    EpistemicNode,
    Fact,
    Inference,
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
SUCCESSION_STATUSES: frozenset[NodeStatus] = frozenset({
    NodeStatus.ACTIVE, NodeStatus.HISTORICAL,
})

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
    rather than a presentational one — see the entry in ISSUES.md #53.
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


def _revised(
    interval: ValidityInterval, endpoint: str, at: datetime
) -> ValidityInterval:
    """The interval with one endpoint filled in, rebuilt so it is checked.

    `model_copy(update=...)` skips validation, and the validation is the point:
    a period that would start at or after it ends, or one whose own witness the
    new endpoint excludes, must raise here rather than reach storage.

    The basis becomes `inferred` because that is what this boundary is (§8) —
    worked out from two sources read together rather than copied from either.
    """
    return ValidityInterval.model_validate(
        interval.model_dump()
        | {endpoint: PreciseInstant(at=at), "basis": IntervalBasis.INFERRED}
    )


def _periods(sources: Sequence[SourceValidity]) -> list[tuple[str, ValidityInterval]]:
    """Every period a node holds, each paired with the source asserting it."""
    return [
        (source.source_id, interval)
        for source in sources
        for interval in source.intervals
    ]


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
    validity: dict[str, list[SourceValidity]],
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
            node=earlier, source_id=source_id, interval=interval,
            endpoint="end", at=opens[1],
            because=later, because_source_id=opens[0],
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
            node=later, source_id=source_id, interval=interval,
            endpoint="start", at=closes[1],
            because=earlier, because_source_id=closes[0],
        )
        if proposal is not None:
            proposals.append(proposal)

    return proposals


async def propose_boundaries(
    storage: StorageBackend,
) -> list[BoundaryProposal]:
    """Where a succession lets one claim's period close and the next one's open.

    Reads only, in four batched queries: the claims on both sides of a
    succession, their lineage edges, and their validity.

    A proposal needs a succession edge *and* a date, so a graph with either and
    not the other produces nothing. That is the common case and stays the common
    case: this is sparse by design, like everything else validity touches.
    """
    holders: dict[str, EpistemicNode] = {}
    for status in sorted(SUCCESSION_STATUSES, key=lambda s: s.value):
        for node in await storage.query_nodes(status=status):
            if isinstance(node, _CAN_BE_TRUE):
                holders[node.id] = node
    if not holders:
        return []

    successions = await storage.get_edges_for(
        list(holders), direction="from", edge_type=EdgeType.TEMPORALLY_FOLLOWED_BY
    )
    pairs = [
        (holders[edge.src_id], holders[edge.dst_id])
        for edges in successions.values()
        for edge in edges
        # A successor the graph no longer holds as a live or historical claim —
        # merged away, or corrected — is not something to align a period to.
        if edge.dst_id in holders
    ]
    if not pairs:
        return []

    validity = await validity_for(
        list(dict.fromkeys(node.id for pair in pairs for node in pair)), storage
    )
    return [
        proposal
        for earlier, later in pairs
        for proposal in _across_one_succession(earlier, later, validity)
    ]


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
        return BoundaryRefused(
            node_id=node_id, reason=f"'{endpoint}' is not an endpoint"
        )

    node = await storage.get_node(node_id)
    if node is None or node.status not in SUCCESSION_STATUSES:
        return BoundaryRefused(
            node_id=node_id,
            reason="no such claim, or not one a succession can be about",
        )

    edges = [
        edge
        for edge in await storage.get_edges_from(
            node_id, edge_type=EdgeType.SOURCED_FROM
        )
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
        if interval.timeline_id == timeline_id
        and is_open_boundary(getattr(interval, endpoint))
    ]
    if len(candidates) != 1:
        return BoundaryRefused(
            node_id=node_id,
            reason=(
                f"{len(candidates)} periods from that source are still open at "
                f"their {endpoint}"
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


def _with_validity(edge: NodeEdge, validity: list[ValidityInterval]) -> NodeEdge:
    """The same edge, same id, carrying a different set of periods.

    Rebound rather than mutated in place: `model_copy` shares the list, so
    appending to it would reach back into whatever the backend handed over.
    """
    return edge.model_copy(update={"validity": validity})
