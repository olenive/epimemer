"""The temporal soundness check over stored inferences (#53 T1 §11).

The graph makes no inferences; the agent does and the graph stores them. So the
defect this catches is not one the engine can prevent at write time — an
inference drawn across *"the city is called Leningrad"* and a claim about 2020
is unsound, and nothing in the model noticed until validity existed. This is the
reason #53 outranks everything else in `ISSUES.md`: not a display defect but a
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
from epimemer.storage.protocol import StorageBackend


class NodeRef(BaseModel):
    """A node named in a flag, with enough of it to judge without a fetch."""

    id: str
    content: str


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


async def _premise_ids(
    inference_ids: Sequence[str], storage: StorageBackend
) -> dict[str, list[str]]:
    """The facts each inference rests on, by whichever edge records it.

    Both directions count, exactly as evidence-staleness already counts them
    (`plan_evidence_stale_edges`): an inference is `derived_from` a fact, and a
    fact `supports` an inference. Two batched queries for the whole set rather
    than two per inference — this runs inside the phase that fails first as a
    graph grows (#39), so it may not be the thing that adds a round-trip per
    node (ISSUES.md #14).
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


def _disjoint_pairs(
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


async def find_unsound_inferences(
    storage: StorageBackend,
) -> list[UnsoundInference]:
    """Active inferences whose premises no source puts in the same period.

    Reads only, in at most four batched queries whatever the graph's size: the
    inferences, their premise edges both ways, those premises' validity, and —
    only if anything can still fire — the premise facts themselves.

    **Validity is read before the facts are**, which is the ordering the cost
    argues for rather than the one the sentence above reads in. Undated premises
    cannot fire, most of the graph is undated and always will be, and this phase
    sits inside the tool that crosses the timeout first (#39) — so on a graph
    with no intervals it fetches no nodes at all, and on one with a few it
    fetches those few. Fetching every premise first and discarding them was
    measurably worse for exactly the graphs that cannot produce a flag.
    """
    inferences = [
        node
        for node in await storage.query_nodes(node_type=NodeType.INFERENCE)
        if isinstance(node, Inference)
    ]
    if not inferences:
        return []

    premise_ids = await _premise_ids([node.id for node in inferences], storage)
    wanted = list(dict.fromkeys(
        premise_id for ids in premise_ids.values() for premise_id in ids
    ))
    if not wanted:
        return []

    validity = await validity_for(wanted, storage)
    # One dated premise is never a pair, so an inference with fewer than two of
    # them is out before anything is fetched.
    dated_premises = {
        inference.id: [
            premise_id
            for premise_id in premise_ids[inference.id]
            if premise_id in validity
        ]
        for inference in inferences
    }
    candidates = {
        inference_id: ids
        for inference_id, ids in dated_premises.items()
        if len(ids) > 1
    }
    if not candidates:
        return []

    facts: dict[str, EpistemicNode] = await storage.get_nodes(
        list(dict.fromkeys(
            premise_id for ids in candidates.values() for premise_id in ids
        ))
    )

    flagged: list[UnsoundInference] = []
    for inference in inferences:
        dated = [
            PremisePeriods(
                id=premise_id,
                content=facts[premise_id].content,
                # The existential union per premise: what *some* source asserts.
                # Named in `assertions_are_disjoint`, applied here.
                periods=[
                    interval
                    for source in validity[premise_id]
                    for interval in source.intervals
                ],
            )
            for premise_id in candidates.get(inference.id, ())
            # A `supports` edge into an inference can only come from a fact by
            # contract, and the check is here rather than trusted because a
            # hand-written `link` is not bound by the contract.
            if isinstance(facts.get(premise_id), Fact)
        ]
        pairs = _disjoint_pairs(dated)
        if pairs:
            flagged.append(UnsoundInference(
                inference=NodeRef(id=inference.id, content=inference.content),
                disjoint_premises=pairs,
            ))
    return flagged
