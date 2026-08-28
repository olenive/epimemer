"""The temporal soundness check over stored inferences.

The graph makes no inferences; the agent does and the graph stores them. So the
defect this catches is not one the engine can prevent at write time — an
inference drawn across *"the city is called Leningrad"* and a claim about 2020
is unsound, and nothing in the model noticed until validity existed. This is the
reason the validity model mattered more than anything else: not a display
defect but a
soundness defect in the layer the system exists to provide.

**It flags and never blocks, and it never fires on unknown.** Both are
requirements rather than choices. `unknown` is the common outcome of any
comparison here, so a blocking check would be unusable, and one that treated
*cannot be placed* as *disjoint* would be a check on ignorance rather than on
evidence. What fires is narrow and worth reading when it does:
`assertions_are_disjoint` holds only when both premises carry periods and every
cross pair of them provably falls clear.

**It runs at reflect rather than at ingest**, and the motivating case is why: an
inference combining a fact from a 1970 document with one from a 2000 document is
invisible while either is being stored, because the other is not in front of the
agent. Reflect sees the whole graph, which is the only vantage point from which
the two premises are both present.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from epimemer.core.temporal import ValidityInterval, assertions_are_disjoint
from epimemer.core.types import (
    EdgeType,
    EpistemicNode,
    Fact,
    Inference,
    NodeType,
)
from epimemer.pipelines.query.validity import validity_for
from epimemer.pipelines.reflection.review import NodeRef
from epimemer.storage.protocol import StorageBackend


class PremisePeriods(BaseModel):
    """One premise and every period any of its sources asserts it held.

    The periods are the *evidence for the flag* and are reported rather than
    summarised: the agent's decision is whether the inference survives, and a
    verdict with the dates hidden behind it cannot be argued with.
    """

    id: str
    content: str
    periods: list[ValidityInterval] = Field(default_factory=list)


class DisjointPremises(BaseModel):
    """Two premises of one inference that no source asserts were ever both true."""

    a: PremisePeriods
    b: PremisePeriods


class UnsoundInference(BaseModel):
    """An inference resting on premises that never held together, as far as anyone said.

    One entry per inference rather than per pair: the agent's move is about the
    inference — re-derive it, narrow it, or retire it — and splitting one
    decision across several rows invites acting on it several times.
    """

    inference: NodeRef
    disjoint_premises: list[DisjointPremises]


async def premise_ids_for(
    inference_ids: Sequence[str], storage: StorageBackend
) -> dict[str, list[str]]:
    """The facts each inference rests on, by whichever edge records it.

    Both directions count, exactly as evidence-staleness already counts them
    (`plan_evidence_stale_edges`): an inference is `derived_from` a fact, and a
    fact `supports` an inference. Two batched queries for the whole set rather
    than two per inference — this runs inside the phase that fails first as a
    graph grows, so it may not be the thing that adds a round-trip per
    node.
    """
    derived = await storage.get_edges_for(
        list(inference_ids), direction="from", edge_type=EdgeType.DERIVED_FROM
    )
    supported = await storage.get_edges_for(
        list(inference_ids), direction="to", edge_type=EdgeType.SUPPORTS
    )
    return {
        inference_id: list(dict.fromkeys(
            [edge.dst_id for edge in derived[inference_id]]
            + [edge.src_id for edge in supported[inference_id]]
        ))
        for inference_id in inference_ids
    }


def disjoint_pairs(
    premises: Sequence[PremisePeriods],
) -> list[DisjointPremises]:
    """Every pair of dated premises whose asserted periods fall clear of each other.

    Ordered pairs are deliberately not produced: *a* and *b* being disjoint is
    one finding, and reporting it twice would double a worklist for no extra
    information.
    """
    return [
        DisjointPremises(a=one, b=other)
        for index, one in enumerate(premises)
        for other in premises[index + 1 :]
        if assertions_are_disjoint(one.periods, other.periods)
    ]


async def premise_periods_for(
    premise_ids: Sequence[str], storage: StorageBackend
) -> dict[str, PremisePeriods]:
    """The premises among these that somebody dated, keyed by id.

    An undated premise is simply absent, which is the whole reason this returns
    a map rather than a list: *nobody placed this claim* and *this claim has no
    periods* are the same row otherwise, and only the first is true.

    **Validity is read before the facts are**, and the ordering is the cost
    argument rather than the sentence order. Undated premises cannot produce a
    finding, most of the graph is undated and always will be, and both callers
    sit inside operations that cross the timeout first — so on a graph with no
    intervals this fetches no nodes at all. Fetching every premise first and
    discarding them was measurably worse for exactly the graphs that cannot
    produce a flag.
    """
    if not premise_ids:
        return {}
    validity = await validity_for(list(premise_ids), storage)
    if not validity:
        return {}
    facts: dict[str, EpistemicNode] = await storage.get_nodes(list(validity))
    return {
        premise_id: PremisePeriods(
            id=premise_id,
            content=facts[premise_id].content,
            # The existential union per premise: what *some* source asserts.
            # Named in `assertions_are_disjoint`, applied here.
            periods=[
                interval
                for source in sources
                for interval in source.intervals
            ],
        )
        for premise_id, sources in validity.items()
        # A `supports` edge into an inference can only come from a fact by
        # contract, and the check is here rather than trusted because a
        # hand-written `link` is not bound by the contract.
        if isinstance(facts.get(premise_id), Fact)
    }


async def find_unsound_inferences(
    storage: StorageBackend,
) -> list[UnsoundInference]:
    """Active inferences whose premises no source puts in the same period.

    Reads only, in at most four batched queries whatever the graph's size: the
    inferences, their premise edges both ways, and then whatever
    `premise_periods_for` needs — which is where the ordering that keeps an
    undated graph free of node fetches lives.

    **The same disjointness the inference-merge advisory reports**, computed by
    the same two functions. This one asks it of an inference that exists;
    nomination asks it of one that would. A finding here is correct rather than
    manufactured either way: the agent wrote the claim over the combined
    premises, so premises that never held together make it genuinely unsound.
    """
    inferences = [
        node
        for node in await storage.query_nodes(node_type=NodeType.INFERENCE)
        if isinstance(node, Inference)
    ]
    if not inferences:
        return []

    premise_ids = await premise_ids_for([node.id for node in inferences], storage)
    wanted = list(dict.fromkeys(
        premise_id for ids in premise_ids.values() for premise_id in ids
    ))
    dated = await premise_periods_for(wanted, storage)
    if not dated:
        return []

    flagged: list[UnsoundInference] = []
    for inference in inferences:
        pairs = disjoint_pairs([
            dated[premise_id]
            for premise_id in premise_ids[inference.id]
            if premise_id in dated
        ])
        if pairs:
            flagged.append(UnsoundInference(
                inference=NodeRef(id=inference.id, content=inference.content),
                disjoint_premises=pairs,
            ))
    return flagged
