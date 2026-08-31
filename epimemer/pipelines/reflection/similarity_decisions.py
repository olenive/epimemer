"""What an agent decided about a nominated pair (`REVIEW_MODE.md` §1).

`reflect` nominates pairs and the agent classifies each one. Six of the seven
verdicts had an action; the seventh — *compatible*, these merely look alike —
had none, by omission rather than by design. So a decline was recorded nowhere,
and `already_linked` in `contradiction_detection` re-offered the same pair on
every pass. Measured 2026-08-21: of eighteen pairs nominated on `memory`, five
merged and **thirteen were declined and vanished.**

**A decline is two populations, not one, and that is the whole design.** The
obvious fix — one `similarity` edge per decline — has two readers that want
opposite breadth:

- the nomination sweep wants it **broad**: suppress every pair anybody assessed;
- `corroboration.py` wants it **narrow**: its neighbourhood is *restatements of
  one claim*, and a wrong member overstates the count (`docs/RETRIEVAL.md` §8).

Serve both from one edge and *"these two are different claims"* starts
corroborating. That is **manufactured support** — the failure `fact_dedup`'s
header calls the worst this system can produce, because a false unification does
not lose information, it inverts the quantity corroboration measures.

So the verdict picks the edges:

| Verdict      | The pair is                          | Writes                    |
|--------------|--------------------------------------|---------------------------|
| `one_claim`  | genuinely one claim, unmergeable     | `similarity` + `assessed` |
| `distinct`   | different claims that look alike     | `assessed` only           |
| `distinct`, over a standing `one_claim` | **withdrawn**   | `retracted_similarity` + `assessed` |

**The third row is a retraction, and it is the only conditional write here**
. `distinct` on a pair that already carries a `similarity` edge used to be
*refused*, because nothing could unmake a `one_claim` and writing `assessed`
beside a standing `similarity` would have reported success while the pair went
on corroborating. The edge is still not deleted — nothing here deletes — so the
retraction is a second edge that disqualifies the first, which is the mechanism
`corroboration.py` already runs for `contradiction` and for exactly this reason:
*"the `similarity` edge written before the verdict stays in the graph"*.

**A retraction is terminal**, and the asymmetry is the design rather than an
omission. Nothing re-asserts `one_claim` over one, because the two directions
fail differently: a false unification manufactures agreement — the worst failure
this system has — while a withdrawn one under-counts. Under-counting is the
direction dedup chose when it left the pre-`claim_kind` corpus unmergeable, and it
is the direction to keep choosing.

**Suppression is unaffected**, which is what keeps the retraction narrow. The
`assessed` edge stays and the pair stays out of every future nomination: the
agent has now judged it twice, and re-offering it would restart the treadmill
the `assessed` edge closed. A retraction changes what corroboration counts, and
nothing else.

`assessed` is a denormalised suppression index, and it is legitimate as one
because it is immutable and append-only: it cannot drift from the decision
journal that will also record it (§3.4). The journal is the audit record; this
edge is what the sweep reads without a journal query.

**Nothing here writes on its own initiative.** These edges record a *judgment*.
A sweep that wrote them for every pair over the bar would fill the graph with
assertions nobody made, and suppress its own future nominations while doing it.
Similarity nominates; the agent judges.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    NOMINATED_STATUSES,
    EdgeType,
    JudgeRef,
    NodeEdge,
)
from epimemer.pipelines.reflection.review import same_frame
from epimemer.storage.protocol import StorageBackend

# An edge of any of these between two nodes means the pair has been put in front
# of somebody and decided. Nominating it again would be asking a question that
# has an answer. Deliberately wider than the edges corroboration reads: this set
# is about *suppression*, and `assessed` and `variant_of` suppress without
# supporting.
ALREADY_JUDGED_EDGE_TYPES: tuple[EdgeType, ...] = (
    EdgeType.SIMILARITY,
    EdgeType.CONTRADICTION,
    EdgeType.VARIANT_OF,
    EdgeType.ASSESSED,
)


async def already_judged_pairs(
    node_ids: Sequence[str], storage: StorageBackend
) -> set[frozenset[str]]:
    """Every pair among these that somebody has already decided about.

    **One reader for the suppression this module writes.** Each sweep used to
    carry its own copy, and the copies were the defect: the fact sweep read it,
    the inference sweep read it, and the topic sweep never had — so a topic
    verdict wrote its `assessed` edge, reported success, and the pair came back
    on the next reflect anyway. A verdict with a writer and no reader is
    indistinguishable from one nobody recorded, except that it also says it
    worked.

    Taking the node ids rather than a node type is what makes it one function:
    the suppression is a property of the pair, not of what the pair is made of.

    Two queries per edge type, both directions, for the whole set — the edge
    type is part of the query rather than a filter over every edge each node
    has. If that ever costs more than it saves, the lever is one untyped
    `get_edges_for` per direction, and that is a trade to make on a measurement.
    """
    judged: set[frozenset[str]] = set()
    ids = list(node_ids)
    if not ids:
        return judged
    for edge_type in ALREADY_JUDGED_EDGE_TYPES:
        for direction in ("from", "to"):
            found = await storage.get_edges_for(ids, direction=direction, edge_type=edge_type)
            for edges in found.values():
                for edge in edges:
                    judged.add(frozenset({edge.src_id, edge.dst_id}))
    return judged


# The verdict is rejected rather than defaulted when it is not one of these. A
# default would pick one of two writes that differ in exactly the way this
# module exists to keep apart, on behalf of an agent that said neither.
SIMILARITY_VERDICTS: tuple[str, ...] = ("one_claim", "distinct")

# Which edges each verdict writes. `assessed` is in both: every verdict is an
# assessment, and suppression is what both populations have in common.
VERDICT_EDGES: dict[str, tuple[EdgeType, ...]] = {
    "one_claim": (EdgeType.SIMILARITY, EdgeType.ASSESSED),
    "distinct": (EdgeType.ASSESSED,),
}


class SimilarityRefused(BaseModel):
    """Why one similarity decision was not recorded.

    Prose rather than a code, matching `BoundaryRefused` and `MergeRefused`: the
    reasons do not form a vocabulary anything branches on, and each is useful
    only to the extent it says which of several near-identical situations
    occurred.
    """

    pair: list[str]
    reason: str


class SimilarityRecorded(BaseModel):
    """What one accepted decision wrote.

    `edges_created` counts only edges that did not already exist, so a batch
    replayed after a timeout reports zero rather than claiming to have written
    what was already there.

    `retracted` says **this call** withdrew a standing `one_claim` rather than
    judging a fresh pair. The caller needs it because the two are
    different decisions to journal — a verdict and the withdrawal of one — and
    they are indistinguishable from the verdict string alone. A `distinct`
    repeated over a pair already withdrawn reports `False`: it decided nothing
    new, and a second retraction row would read as a second withdrawal.
    """

    pair: list[str]
    verdict: str
    edge_ids: dict[str, str]
    edges_created: int
    retracted: bool = False


async def symmetric_edge_between(
    a_id: str, b_id: str, edge_type: EdgeType, storage: StorageBackend
) -> NodeEdge | None:
    """An existing edge of ``edge_type`` between a and b, in either direction."""
    for edge in await storage.get_edges_from(a_id, edge_type=edge_type):
        if edge.dst_id == b_id:
            return edge
    for edge in await storage.get_edges_from(b_id, edge_type=edge_type):
        if edge.dst_id == a_id:
            return edge
    return None


async def apply_similarity_decision(
    storage: StorageBackend,
    *,
    a_id: str,
    b_id: str,
    verdict: str,
    because: str,
    judge: JudgeRef | None = None,
) -> SimilarityRefused | SimilarityRecorded:
    """Record one verdict about one nominated pair, or say why it was not recorded.

    Refusals are ordered permanent-first, on `fact_dedup`'s reasoning: a
    malformed request will never become well-formed, while a pair whose node is
    retired may be nominable again after a `restore`. Reporting the second while
    the first also stands sends an agent to do work that changes nothing.
    """
    pair = [a_id, b_id]

    if verdict not in SIMILARITY_VERDICTS:
        return SimilarityRefused(
            pair=pair,
            reason=(
                f"'{verdict}' is not a verdict about a pair. Expected one of: "
                f"{', '.join(SIMILARITY_VERDICTS)} — 'one_claim' where the two "
                f"say the same thing and something blocked the merge, "
                f"'distinct' where they merely look alike."
            ),
        )
    if not because.strip():
        return SimilarityRefused(
            pair=pair,
            reason=(
                "`because` is required: this edge suppresses the pair from every "
                "future nomination, so the graph has to carry why."
            ),
        )
    if a_id == b_id:
        return SimilarityRefused(
            pair=pair, reason="a node is already itself; a pair needs two nodes."
        )

    a = await storage.get_node(a_id)
    b = await storage.get_node(b_id)
    missing = [node_id for node_id, node in ((a_id, a), (b_id, b)) if node is None]
    if missing:
        return SimilarityRefused(pair=pair, reason=f"no such node: {', '.join(missing)}.")

    # The sweep only ever nominates ACTIVE and HISTORICAL (`NOMINATED_STATUSES`),
    # so an `assessed` edge touching anything else suppresses nothing that could
    # have been offered — and a `similarity` edge would still corroborate. A
    # HISTORICAL side is deliberately allowed: the recurrence sweep nominates
    # active/historical pairs, and refusing them would leave exactly half the
    # treadmill this module exists to stop still running.
    unnominable = [
        f"{node.id} is {node.status.value}"
        for node in (a, b)
        if node is not None and node.status not in NOMINATED_STATUSES
    ]
    if unnominable:
        return SimilarityRefused(
            pair=pair,
            reason=(
                f"{'; '.join(unnominable)}, and only "
                f"{'/'.join(sorted(s.value for s in NOMINATED_STATUSES))} nodes are "
                f"ever nominated — recording a judgment here would suppress "
                f"nothing and could still be read as support."
            ),
        )

    standing_similarity = await symmetric_edge_between(a_id, b_id, EdgeType.SIMILARITY, storage)
    standing_retraction = await symmetric_edge_between(
        a_id, b_id, EdgeType.RETRACTED_SIMILARITY, storage
    )

    if verdict == "one_claim" and not await same_frame(a_id, b_id, storage):
        # A `similarity` edge across frames is a fiction corroborating a fact.
        # `variant_of` is the relation for it, and corroboration already
        # disqualifies partners that carry one — which is the difference
        # between the two being kept apart and them being kept apart *and
        # counted*.
        return SimilarityRefused(
            pair=pair,
            reason=(
                "these are in different metacontext frames, so 'one_claim' would "
                "have one frame's resolution corroborate the other's — "
                "record_variant is the relation for a cross-frame pair. "
                "'distinct' is available and writes no similarity."
            ),
        )

    if verdict == "one_claim" and standing_retraction is not None:
        # A retraction is terminal, and this is where that is enforced. The two
        # directions are not symmetric: withdrawing a `one_claim` costs a count
        # the graph will no longer make, while re-asserting one over a
        # withdrawal manufactures agreement — and manufactured agreement does
        # not lose information, it inverts the quantity corroboration measures.
        return SimilarityRefused(
            pair=pair,
            reason=(
                f"an earlier 'one_claim' verdict about this pair was withdrawn "
                f"({standing_retraction.id}), and nothing re-asserts one. If "
                f"these really are one claim, merge_facts is the call that says "
                f"so; otherwise raise it with the user rather than working "
                f"around it."
            ),
        )

    # `distinct` over a standing `one_claim` **retracts** it. The
    # `similarity` edge stays — nothing here deletes — and the retraction edge
    # is what stops corroboration counting it, the same way `contradiction`
    # already does for a pair judged the other way round.
    retracting = verdict == "distinct" and standing_similarity is not None
    edge_types = VERDICT_EDGES[verdict]
    if retracting:
        edge_types = (EdgeType.RETRACTED_SIMILARITY, *edge_types)

    edge_ids: dict[str, str] = {}
    edges_created = 0
    for edge_type in edge_types:
        existing = (
            standing_similarity
            if edge_type is EdgeType.SIMILARITY
            else await symmetric_edge_between(a_id, b_id, edge_type, storage)
        )
        if existing is not None:
            edge_ids[edge_type.value] = existing.id
            if edge_type is EdgeType.RETRACTED_SIMILARITY:
                # Already withdrawn. This call decided nothing new, so it must
                # not report a retraction the caller would journal a second time.
                retracting = False
            continue
        # The verdict rides on the `assessed` edge as well as being implied by
        # which edges exist: a reader holding one edge should not have to look
        # for the absence of another to know what was decided.
        edge = NodeEdge(
            src_id=a_id,
            dst_id=b_id,
            type=edge_type,
            judged_by=judge,
            metadata={"verdict": verdict, "because": because},
        )
        await storage.store_edge(edge)
        edge_ids[edge_type.value] = edge.id
        edges_created += 1

    return SimilarityRecorded(
        pair=pair,
        verdict=verdict,
        edge_ids=edge_ids,
        edges_created=edges_created,
        retracted=retracting,
    )
