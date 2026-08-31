"""Nominating and gating a merge of two inferences.

An inference **is** its derivation, so collapsing two of them migrates both sets
of `derived_from` edges onto the survivor: a merge of A resting on `{F1}` and B
resting on `{F2}` leaves one node resting on `{F1, F2}` — a combination neither
original had. Usually that is right and good, two pieces of evidence for one
conclusion. It goes wrong in exactly one **checkable** case: `F1` and `F2` are
both dated and their asserted periods provably fall clear of each other.

**That is a warning rather than a rule, and the distinction is the whole
design.** The first reading of this treated the union as a *fabrication* — the
merge inventing a derivation nobody made — and concluded inferences must never
merge. It was wrong, because the merge does not silently preserve two arguments:
**the agent writes fresh content**, asserting one claim over the combined
premises. If those premises never held together, the resulting inference is
*genuinely* unsound and `find_unsound_inferences` is right to say so. So the
mechanism is sound and the danger is a specific computable outcome, which is
what a warning addresses and a refusal does not — the honest response to *these
premises never held together* is often to narrow the merged claim's wording or
period, which the agent can only do by writing content, which is what it is
already doing. Refusing would block a merge the agent could have fixed.

**And the warning is pre-decision.** The disjointness is computable from the
graph before anything is proposed, so it rides along with the candidate rather
than arriving as a rejection to re-propose past. A round trip to deliver
information already in hand is latency bought for nothing.

**Nomination is scoped to shared evidence, never a global sweep**, for three
reasons that are not interchangeable:

- It is the case that actually arises. A fact merge collects duplicate
  inferences onto one survivor and flags each with `evidence_merged`; that is
  the population worth reviewing, and it did not exist until facts merged.
- It is cheap. One batched premise read, grouped by premise id, comparing only
  within groups — where a global sweep would be quadratic in *all* inferences,
  with nothing but the response cap standing between it and a runaway.
- A global sweep nominates nothing. Measured on both real graphs: 123 active
  inferences, 5,053 pairs, **zero** at the nomination bar (p50 0.16–0.24, p99
  0.44–0.55, max 0.66). The top-scoring pairs are not duplicates at all — they
  share vocabulary and say different things.

**There is no `claim_kind` analogue, and that is a decision rather than an
omission.** `claim_kind` exists because interval union is correct for a state
and fabricating for an event, and that union happens *mechanically* on the
`sourced_from` edges. The inference equivalent — whether combining premises is
legitimate — is not mechanical: the agent writes the merged claim, and the
question of tense and generality is answered in that text. A stored judgment
would freeze at ingest what the merge itself decides.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field

from epimemer.core.advisories import Advisory, AdvisoryKind
from epimemer.core.types import (
    DEFAULT_MERGE_CYCLE_LIMIT,
    Inference,
    NodeStatus,
    NodeType,
    completed_merge_cycles,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.reflection.fact_dedup import MergeRefused
from epimemer.pipelines.reflection.pair_scoring import stack_uniform_width
from epimemer.pipelines.reflection.review import (
    SIMILARITY_NOMINATION_THRESHOLD,
    NodeRef,
    frames_for,
)
from epimemer.pipelines.reflection.similarity_decisions import already_judged_pairs
from epimemer.pipelines.reflection.soundness import (
    DisjointPremises,
    PremisePeriods,
    disjoint_pairs,
    premise_ids_for,
    premise_periods_for,
)
from epimemer.pipelines.reflection.topic_consolidation import (
    all_pairs_above_threshold,
)
from epimemer.storage.protocol import StorageBackend


class InferenceMergeCandidate(BaseModel):
    """Two near-identical inferences drawn on at least one common premise.

    A proposal, never a verdict: what the graph can see is that these two rest
    on the same evidence and say nearly the same thing. Whether they are one
    claim is the agent's judgment, made with both in front of it.
    """

    inferences: list[NodeRef]
    # The premises they have in common — why this pair was looked at at all.
    shared_premises: list[NodeRef] = Field(default_factory=list)
    similarity: float
    # Computed before the agent decides, which is the point of it.
    warnings: list[Advisory] = Field(default_factory=list)


def _disjoint_advisory(pair: DisjointPremises, subjects: Sequence[str]) -> Advisory:
    """One `DISJOINT_PREMISES` advisory, with the periods as structured evidence.

    The message names the premises and stops there. Rendering the dates into it
    would mean branching on the endpoint kind, which nothing downstream of
    `ImpreciseInstant` does — that discipline is what lets a new kind of instant
    touch one module, and a test enforces it by looking for the discriminator's
    name — so the intervals travel in `detail`, where the reviewing agent wanted
    them structured anyway.
    """
    return Advisory(
        kind=AdvisoryKind.DISJOINT_PREMISES,
        message=(
            f"No source asserts these premises held at a common moment: "
            f'"{pair.a.content}" and "{pair.b.content}". A claim drawn over '
            f"both would be flagged unsound — narrow the wording or the period, "
            f"or merge only the inferences that share a premise set."
        ),
        subjects=list(subjects),
        detail=pair.model_dump(mode="json"),
    )


def disjoint_advisories(
    source_ids: Sequence[str],
    premise_ids: Sequence[str],
    dated: dict[str, PremisePeriods],
) -> list[Advisory]:
    """The advisories for a merge over `premise_ids`, given periods already read.

    Pure, because the nomination sweep answers this for many candidate pairs
    that share premises: reading the periods once and deciding many times is the
    difference between one batched query and one per pair.

    One advisory per disjoint pair rather than one for the merge — the pairs are
    what an agent can act on individually, and a single advisory naming six
    premises says nothing about which two are the problem.
    """
    return [
        _disjoint_advisory(pair, source_ids)
        for pair in disjoint_pairs(
            [dated[premise_id] for premise_id in premise_ids if premise_id in dated]
        )
    ]


async def merge_advisories(sources: Sequence[Inference], storage: StorageBackend) -> list[Advisory]:
    """What is worth knowing about collapsing these into one, before it happens.

    Reads the union of the sources' premises, because the union is what the
    survivor would rest on. The single-merge path; nomination reads in bulk and
    calls `disjoint_advisories` directly.
    """
    premises = await premise_ids_for([node.id for node in sources], storage)
    combined = list(dict.fromkeys(premise_id for ids in premises.values() for premise_id in ids))
    return disjoint_advisories(
        [node.id for node in sources],
        combined,
        await premise_periods_for(combined, storage),
    )


async def merge_refusal(
    sources: Sequence[Inference],
    storage: StorageBackend,
    *,
    model_id: str,
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    cycle_limit: int = DEFAULT_MERGE_CYCLE_LIMIT,
) -> MergeRefused | None:
    """Why these inferences must not be collapsed into one, or `None` if nothing objects.

    The same gate as `fact_dedup.merge_refusal` minus one rung, and the shared
    ones are shared for the same reasons — so the differences are what is worth
    reading here:

    - **No `claim_kind`.** See the module header; it is a decision, not a gap.
    - **Disjoint premises do not appear.** They are an advisory, delivered with
      the nomination, and refusing on them would block the merge an agent could
      have fixed by writing narrower content.

    `None` is not an endorsement. Two inferences agreeing may be independent
    support rather than redundancy — which is the thing corroboration exists to
    count — and no cosine separates the two. What this answers is the narrower
    question: is there a reason this merge would be wrong regardless of how good
    the agent's judgment is.
    """
    distinct = {node.id for node in sources}
    if len(distinct) < 2:
        return MergeRefused(
            reason=(
                f"a merge collapses several inferences into one and this names "
                f"{len(distinct)}; a single inference is already itself."
            )
        )

    retired = [node for node in sources if node.status is not NodeStatus.ACTIVE]
    if retired:
        # A retired inference has been ruled on — superseded by a better
        # derivation, archived as spent, or merged already. Folding one back
        # into the active set through a merge would undo that ruling silently.
        statuses = ", ".join(sorted({node.status.value for node in retired}))
        return MergeRefused(
            reason=(
                f"only active inferences merge, and {len(retired)} of these are "
                f"{statuses}. A retired derivation has been ruled on; bringing "
                f"it back is `restore`."
            )
        )

    frames = await frames_for([node.id for node in sources], storage)
    if len({frozenset(frames[node.id]) for node in sources}) > 1:
        # Exactly `fact_dedup`'s rule and for exactly its reason: a merge
        # inherits the union of its sources' frames, so collapsing a claim about
        # base reality into one framed as fiction leaves a node asserting both.
        # Two perspectives reaching the same conclusion about different worlds
        # are two conclusions.
        return MergeRefused(
            reason=(
                "these inferences do not stand in exactly the same set of "
                "frames, and a merged node would inherit the union of them — "
                "asserting in one world what was only ever derived in another."
            )
        )

    oscillating = [node for node in sources if completed_merge_cycles(node) >= cycle_limit]
    if oscillating:
        counts = ", ".join(str(completed_merge_cycles(node)) for node in oscillating)
        return MergeRefused(
            reason=(
                f"{len(oscillating)} of these inferences have already been "
                f"merged and un-merged before ({counts} times), reaching the "
                f"merge_cycle_limit of {cycle_limit} this merge was gated at. "
                f"Merging again is likely to be reversed again. Ask the user "
                f"before proceeding — and if the merge is right, the limit is "
                f"configurable per graph."
            )
        )

    if not await all_pairs_above_threshold(list(sources), storage, model_id, similarity_threshold):
        return MergeRefused(
            reason=(
                f"not every pair reaches the {similarity_threshold} similarity "
                f"bar this merge was gated at (or one of them has no stored "
                f"embedding). Inferences below the bar are not nominated as "
                f"candidates, so `SIMILARITY` and keeping both is the action "
                f"available here."
            )
        )

    return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two stored vectors, zero-safe.

    A pair at a time rather than `pair_scoring.similar_pairs`, because the
    candidate set here is *grouped* and sparse: scoring the full matrix to read
    a few cells off it would reintroduce exactly the quadratic this nomination
    is shaped to avoid.
    """
    import numpy as np

    left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    norms = float(np.linalg.norm(left) * np.linalg.norm(right))
    return 0.0 if norms == 0.0 else float(left @ right / norms)


async def nominate_inference_merges(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    model_id: str | None = None,
) -> list[InferenceMergeCandidate]:
    """Near-identical active inferences sharing a premise, each with its advisory.

    Reads only. The work is bounded by how many inferences rest on any one
    premise rather than by the graph: a premise supporting *k* inferences
    contributes k(k-1)/2 pairs, and the sum over premises is what a fact merge
    concentrates.

    **That bound is real and is not treated as sufficient.** The response is
    capped in `reflect` like every other pair-built list, because *every pair
    list is capped* is a simpler invariant to hold than *capped except where a
    grouping argument says otherwise* — and this ceiling rises in exactly the
    graphs the list is for, since a heavily merged graph is one that
    concentrates inferences onto surviving premises.
    """
    effective_model_id = model_id or embedding_provider.model_id

    inferences = [
        node
        for node in await storage.query_nodes(
            node_type=NodeType.INFERENCE, status=NodeStatus.ACTIVE
        )
        if isinstance(node, Inference)
    ]
    if len(inferences) < 2:
        return []

    by_id = {node.id: node for node in inferences}
    premises = await premise_ids_for(list(by_id), storage)

    # Grouped by premise, which is the whole shape of this sweep: a pair is a
    # candidate because something they both rest on says so, never because the
    # graph compared everything to everything.
    holders: dict[str, list[str]] = {}
    for inference_id, premise_ids in premises.items():
        for premise_id in premise_ids:
            holders.setdefault(premise_id, []).append(inference_id)

    shared: dict[frozenset[str], list[str]] = {}
    for premise_id, group in holders.items():
        if len(group) < 2:
            continue
        for index, one in enumerate(group):
            for other in group[index + 1 :]:
                shared.setdefault(frozenset({one, other}), []).append(premise_id)
    if not shared:
        return []

    # Asked only about the inferences that formed a pair, not about every active
    # one. The grouping has already cut the set — often to a handful out of
    # hundreds — and this is eight edge queries, so widening it to the whole
    # population would undo the saving the grouping is here for.
    grouped = list(dict.fromkeys(node_id for pair in shared for node_id in sorted(pair)))
    judged = await already_judged_pairs(grouped, storage)
    pairs = [pair for pair in shared if pair not in judged]
    if not pairs:
        return []

    involved = list(dict.fromkeys(node_id for pair in pairs for node_id in sorted(pair)))
    stored = await storage.get_embeddings_for_items(involved, model_id=effective_model_id)
    vectors = {node_id: stored[node_id][0].vector for node_id in involved if stored[node_id]}
    # Uniform width for the same reason the batched scorer needs it: a stored
    # vector from a different model has a different length, and comparing across
    # them produces a number that means nothing.
    kept, _ = stack_uniform_width(involved, vectors)
    comparable = set(kept)

    scored: list[tuple[frozenset[str], float]] = []
    for pair in pairs:
        one, other = sorted(pair)
        if one not in comparable or other not in comparable:
            continue
        score = _cosine(vectors[one], vectors[other])
        if score >= similarity_threshold:
            scored.append((pair, score))
    if not scored:
        return []

    # Frames are read once for everything still standing, and a cross-frame pair
    # is dropped here rather than offered: `merge_refusal` would refuse it, and
    # nominating what the tool refuses is a worklist that cannot be worked.
    surviving = list(dict.fromkeys(node_id for pair, _ in scored for node_id in sorted(pair)))
    frames = await frames_for(surviving, storage)
    same_frames = [
        (pair, score)
        for pair, score in scored
        if len({frozenset(frames[node_id]) for node_id in pair}) == 1
    ]
    if not same_frames:
        return []

    premise_nodes = await storage.get_nodes(
        list(dict.fromkeys(premise_id for pair, _ in same_frames for premise_id in shared[pair]))
    )
    # Every premise any surviving candidate would rest on — the union, not the
    # intersection, because the union is what the survivor inherits. Read once
    # for the whole sweep, which is what keeps the advisory free per pair.
    dated = await premise_periods_for(
        list(
            dict.fromkeys(
                premise_id
                for pair, _ in same_frames
                for node_id in pair
                for premise_id in premises[node_id]
            )
        ),
        storage,
    )

    candidates = [
        InferenceMergeCandidate(
            inferences=[
                NodeRef(id=node_id, content=by_id[node_id].content) for node_id in sorted(pair)
            ],
            shared_premises=[
                NodeRef(id=premise_id, content=premise_nodes[premise_id].content)
                for premise_id in shared[pair]
                if premise_id in premise_nodes
            ],
            similarity=round(score, 4),
            warnings=disjoint_advisories(
                sorted(pair),
                list(
                    dict.fromkeys(
                        premise_id for node_id in sorted(pair) for premise_id in premises[node_id]
                    )
                ),
                dated,
            ),
        )
        for pair, score in same_frames
    ]
    # Highest scoring first, matching every other nominee list, so a caller that
    # stops reading early stops on the weakest candidates rather than a slice.
    candidates.sort(key=lambda candidate: candidate.similarity, reverse=True)
    return candidates
