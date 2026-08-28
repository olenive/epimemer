"""Domain helpers for the epistemic review loop (see REVIEW_EPISTEMIC.md).

Pure planning functions (reads only) that compute the edges / edge-ids a
supersession or resolution must apply atomically.
"""

from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel

from epimemer.core.types import (
    EdgeType,
    EpistemicNode,
    Inference,
    NodeEdge,
    NodeStatus,
    SUPERSEDED_STATUSES,
)
from epimemer.storage.protocol import StorageBackend


# How similar two facts must be before the loop will offer them to each other as
# candidates (§5.1). **Every nomination path and the merge gate read this one
# number.** It lives here rather than as a literal default on `check_conflicts`
# because more readers kept arriving: a fact merge refuses anything below the
# bar, and the rule it applies is *"a merge may only collapse what could have
# been nominated"* — which is a statement about this number, not a coincidence
# that happens to share its value.
#
# **It was two numbers until 2026-08-21, and the pair was unsound rather than
# untidy.** `check_conflicts` and `merge_facts` used 0.83 while reflect's
# contradiction and recurrence sweeps used a literal 0.80, so a pair scoring
# 0.81 was nominated by reflect, judged `redundant` by the agent, and then
# refused by `merge_facts` — with a message saying the graph would never have
# offered the two as candidates, which it had just done. Unified downwards, to
# 0.80: the gate is not a second opinion on the agent's judgment (see
# `fact_dedup.merge_refusal`), so raising the sweeps instead would have narrowed
# contradiction *and* recurrence nomination to buy back a message. The invariant
# to keep, and the one `TestTheSimilarityFloorIsTheNominationBar` pins, is
# **merge floor ≤ every nomination bar**.
SIMILARITY_NOMINATION_THRESHOLD = 0.80


class NodeRef(BaseModel):
    """A node named in a reflect nominee, with enough of it to judge on sight.

    `id` rather than `node_id`, matching every other nominee shape here — the
    key is what `_ids_within` reads to declare what a response carried, and a
    nominee whose ids are spelled differently goes silently undeclared.
    """

    id: str
    content: str


def _unique(ids: list[str]) -> list[str]:
    """De-duplicate ids while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def review_labels_for(
    nodes: Sequence[EpistemicNode],
    storage: StorageBackend,
    *,
    resolve_frames: "FrameResolver | None" = None,
) -> dict[str, dict[str, list[str]]]:
    """Review labels for many nodes at once, keyed by node id (§4.1).

    The batched form, and the one every scan should use: the labels each need
    edges of a *fixed* type at a *fixed* endpoint, which is one query for the
    whole node set rather than one per node. Doing it per node is what made
    `gather_pending_review` the largest single source of round-trips in
    `reflect` — three queries per active node.

    Nodes with no review state are absent from the result rather than mapped to
    an empty dict, so a caller can filter by membership.

    Each label maps to the related node ids the caller can hop to:

    - ``superseded_candidate`` — node has incoming ``supersession_candidate`` edges
      (newer facts proposed to replace it); ids are those newer facts. (Case A)
    - ``evidence_stale`` (inferences only) — node has incoming
      ``evidence_superseded`` flags and/or ``derived_from`` a fact now SUPERSEDED;
      ids are the changed facts. (Case B)
    - ``evidence_merged`` (inferences only) — node has incoming
      ``evidence_merged`` flags: a premise absorbed another claim, so the
      wording under the inference changed while the claim did not; ids are the
      facts that were absorbed. Deliberately *not* folded into
      ``evidence_stale`` — nothing was overturned, and that label is what
      archival nominates on.
    - ``contested`` — node has a ``contradiction`` edge (either direction) to a
      node that is still ACTIVE and in the same frame (an unresolved same-frame
      conflict); ids are the contesting nodes.
    """
    if not nodes:
        return {}

    node_ids = [node.id for node in nodes]
    candidates = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.SUPERSESSION_CANDIDATE
    )
    contradicts_from = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.CONTRADICTION
    )
    contradicts_to = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.CONTRADICTION
    )

    # Only inferences can be evidence-stale, so the two evidence queries are
    # skipped entirely when the set holds none — which keeps the single-node
    # `review_labels` below at the same query count it had before batching.
    inference_ids = [node.id for node in nodes if isinstance(node, Inference)]
    flagged_stale = (
        await storage.get_edges_for(
            inference_ids, direction="to", edge_type=EdgeType.EVIDENCE_SUPERSEDED
        )
        if inference_ids
        else {}
    )
    flagged_merged = (
        await storage.get_edges_for(
            inference_ids, direction="to", edge_type=EdgeType.EVIDENCE_MERGED
        )
        if inference_ids
        else {}
    )
    derived_from = (
        await storage.get_edges_for(
            inference_ids, direction="from", edge_type=EdgeType.DERIVED_FROM
        )
        if inference_ids
        else {}
    )

    # Frames are needed for each node and for whoever contradicts it, and the
    # contradiction partners are only known now that those edges are in hand.
    # A caller that passed its own resolver has its own idea of the set to warm.
    if resolve_frames is None:
        contested_ids = {
            endpoint
            for node in nodes
            for edge in list(contradicts_from[node.id]) + list(contradicts_to[node.id])
            for endpoint in (edge.src_id, edge.dst_id)
        }
        resolve_frames = frame_resolver(
            storage,
            seed=await frames_for(list(contested_ids), storage) if contested_ids else None,
        )

    by_node: dict[str, dict[str, list[str]]] = {}
    for node in nodes:
        labels: dict[str, list[str]] = {}

        if candidates[node.id]:
            labels["superseded_candidate"] = _unique(
                [e.src_id for e in candidates[node.id]]
            )

        if isinstance(node, Inference):
            stale_sources = [e.src_id for e in flagged_stale[node.id]]
            for edge in derived_from[node.id]:
                evidence = await storage.get_node(edge.dst_id)
                # Every supersession kind, not just the legacy one: an
                # inference resting on a claim that has stopped being current
                # is as suspect as one resting on a claim that was wrong.
                if evidence is not None and evidence.status in SUPERSEDED_STATUSES:
                    stale_sources.append(edge.dst_id)
            if stale_sources:
                labels["evidence_stale"] = _unique(stale_sources)
            if flagged_merged[node.id]:
                labels["evidence_merged"] = _unique(
                    [e.src_id for e in flagged_merged[node.id]]
                )

        contesting: list[str] = []
        for edge in list(contradicts_from[node.id]) + list(contradicts_to[node.id]):
            other_id = edge.dst_id if edge.src_id == node.id else edge.src_id
            other = await storage.get_node(other_id)
            if other is None or other.status != NodeStatus.ACTIVE:
                continue  # resolved (the partner was retired) — no longer contested
            if await same_frame(node.id, other_id, storage, resolve=resolve_frames):
                contesting.append(other_id)
        if contesting:
            labels["contested"] = _unique(contesting)

        if labels:
            by_node[node.id] = labels

    return by_node


async def review_labels(
    node: EpistemicNode,
    storage: StorageBackend,
    *,
    resolve_frames: "FrameResolver | None" = None,
) -> dict[str, list[str]]:
    """Epistemic review labels for one active node (REVIEW_EPISTEMIC.md §4.1).

    Edges are the source of truth; this *derives* the labels retrieval surfaces
    so a caller knows a node may be superseded, evidentially stale, or contested
    — without the node ever leaving ACTIVE. See `review_labels_for`, which holds
    the rules and which this is the one-node spelling of; prefer that one when
    labelling a set, so the queries are shared across it.
    """
    return (
        await review_labels_for([node], storage, resolve_frames=resolve_frames)
    ).get(node.id, {})


async def frames_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, set[str]]:
    """`frames_of` for many nodes at once, keyed by node id.

    **This got materially more expensive when frames became explicit**,
    and the number is worth stating rather than assuming: before, almost no node
    carried a `has_metacontext` edge and the read came back nearly empty at any
    graph size; now every ingested node carries exactly one. Measured
    2026-08-28 — 684 nodes: 0.18 ms → 13.6 ms on SurrealDB, 0.28 ms → 4.6 ms
    in-memory; 5,000 nodes: 1.0 ms → 105 ms and 2.1 ms → 34 ms. About 21 µs and
    7 µs per framed node.

    It is bounded by where it is called rather than by the graph: `reflect`
    seeds `frame_resolver` once over the nodes in nominated pairs, and search
    scoping runs over a result set. Neither walks the whole graph. A caller that
    did would want a storage-level frame filter instead.
    """
    tagged = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.HAS_METACONTEXT
    )
    return {
        node_id: {edge.dst_id for edge in edges}
        for node_id, edges in tagged.items()
    }


async def frames_of(node_id: str, storage: StorageBackend) -> set[str]:
    """The metacontext ids a node states, and nothing more.

    **Absence names no frame.** A node with no ``has_metacontext`` edge is a
    node nobody said anything about, which is what absence means everywhere
    else here — an omitted ``confidence`` is unrated, an absent ``judged_by``
    is unknown, an omitted ``claim_kind`` is unjudged. This used to be the one
    exception, answering the base frame for an untagged node and so turning
    silence into an assertion about the real world; requiring the frame at
    ingest made that exception unnecessary, and it was removed.

    The consequence is deliberate and worth stating: a frameless node shares a
    frame with **nothing**, so it is never nominated as contradicting anything,
    never merged, and never returned by a scoped search. That is the honest
    reading of *nobody said*, and it is reachable only on a graph written
    before the requirement — ``epimemer frames declare`` is how such a graph
    stops holding any, and ``graph_stats.nodes_without_frame`` is the check.

    Used to decide whether two nodes share a frame, and so whether an apparent
    conflict is genuine (REVIEW_EPISTEMIC.md §4.3).
    """
    return (await frames_for([node_id], storage))[node_id]


def frame_resolver(
    storage: StorageBackend, *, seed: dict[str, set[str]] | None = None
) -> "FrameResolver":
    """A `frames_of` that answers each node once.

    Frame checks are made per *pair* — of contradiction candidates, of
    contesting nodes — while the nodes involved are drawn from a much smaller
    set. Without this, a pass that compares P pairs over N nodes does O(P)
    lookups instead of O(N).

    `seed` pre-loads answers a caller already has, from `frames_for`; that is
    what takes the remaining O(N) down to one query, since a caller running a
    pass over a node set knows that set upfront. Anything not seeded is still
    resolved on demand, so a partial seed is safe.

    The cache is created by the caller and lives for that one pass, so it cannot
    serve a stale frame to a later operation.
    """
    cache: dict[str, set[str]] = dict(seed) if seed else {}

    async def resolve(node_id: str) -> set[str]:
        if node_id not in cache:
            cache[node_id] = await frames_of(node_id, storage)
        return cache[node_id]

    return resolve


FrameResolver = Callable[[str], Awaitable[set[str]]]


async def same_frame(
    a_id: str,
    b_id: str,
    storage: StorageBackend,
    *,
    resolve: "FrameResolver | None" = None,
) -> bool:
    """Whether two nodes share at least one stated metacontext frame.

    Nodes in disjoint frames — a fiction frame and the real-world one — do not.
    A frame overlap means an apparent contradiction is real; disjoint frames
    mean the two simply coexist.

    **Two frameless nodes do not share a frame**, since neither states one.
    That diverges from the equality test `merge_facts` and topic merge use,
    where two empty sets *are* equal and the merge is allowed — the two
    questions differ, and only for a graph that has not been declared. Overlap
    asks *is this conflict real*, and nothing said cannot make it real; equality
    asks *would merging assert something new*, and merging two claims nobody
    framed asserts nothing new. Both are right; the divergence is visible only
    in the mid-migration state, which `epimemer frames declare` removes.

    Pass `resolve` (from `frame_resolver`) when checking many pairs, so repeated
    nodes are not re-read once per pair.
    """
    lookup = resolve or (lambda node_id: frames_of(node_id, storage))
    return bool(await lookup(a_id) & await lookup(b_id))


async def gather_pending_review(
    storage: StorageBackend,
) -> list[tuple[EpistemicNode, dict[str, list[str]]]]:
    """Active nodes carrying unresolved review state, each with its labels.

    Scans active nodes and returns those whose ``review_labels`` are non-empty —
    the worklist a deliberate review pass (``reflect``) surfaces so the agent can
    resolve them (supersede the loser, re-derive a stale inference, or escalate a
    contested pair to the human). Reads only; resolution is a separate, explicit
    act.
    """
    nodes = list(await storage.query_nodes())
    # No resolver passed: `review_labels_for` warms one from the contradiction
    # partners it finds, which is the set that actually gets frame-checked.
    labels_by_node = await review_labels_for(nodes, storage)
    return [(node, labels_by_node[node.id]) for node in nodes if node.id in labels_by_node]


async def dependent_inference_ids(
    fact_id: str,
    storage: StorageBackend,
) -> list[str]:
    """Ids of the inferences that *directly* rest on ``fact_id``.

    Two edges express the dependency and both count: the inferences the fact
    ``supports``, and those ``derived_from`` it. Direct only — no transitive
    cascade, since a dependent that is itself retired flags its own dependents
    in turn.
    """
    dependent_ids: list[str] = []
    seen: set[str] = set()

    # fact --supports--> inference
    for edge in await storage.get_edges_from(fact_id, edge_type=EdgeType.SUPPORTS):
        node = await storage.get_node(edge.dst_id)
        if isinstance(node, Inference) and node.id not in seen:
            seen.add(node.id)
            dependent_ids.append(node.id)

    # inference --derived_from--> fact
    for edge in await storage.get_edges_to(fact_id, edge_type=EdgeType.DERIVED_FROM):
        node = await storage.get_node(edge.src_id)
        if isinstance(node, Inference) and node.id not in seen:
            seen.add(node.id)
            dependent_ids.append(node.id)

    return dependent_ids


async def plan_evidence_stale_edges(
    superseded_fact_id: str,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Edges flagging inferences whose evidence was just retired (Case B).

    When a fact is superseded, the inferences that directly depend on it become
    suspect. Returns one ``evidence_superseded`` edge (fact → inference) per
    direct dependent.
    """
    return [
        NodeEdge(
            src_id=superseded_fact_id,
            dst_id=inference_id,
            type=EdgeType.EVIDENCE_SUPERSEDED,
        )
        for inference_id in await dependent_inference_ids(superseded_fact_id, storage)
    ]


async def plan_evidence_merged_edges(
    merged_fact_id: str,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Edges telling inferences that a premise absorbed another claim.

    The counterpart of `plan_evidence_stale_edges` for the one event that
    changes a premise without retiring it. Returns one ``evidence_merged`` edge
    (absorbed fact → inference) per direct dependent, and the src is the fact
    that *went away* rather than the survivor: the flag's whole content is
    which wording the inference was drawn from and no longer has.

    **A live check cannot stand in for this the way it does for staleness.**
    `review_labels_for` can notice an inference `derived_from` a retired fact
    because the edge still points at it; a merge migrates that edge onto the
    survivor in the same transaction, and the survivor is ACTIVE. Nothing is
    left to notice, so the flag is the only record there will ever be.
    """
    return [
        NodeEdge(
            src_id=merged_fact_id,
            dst_id=inference_id,
            type=EdgeType.EVIDENCE_MERGED,
        )
        for inference_id in await dependent_inference_ids(merged_fact_id, storage)
    ]


async def find_candidate_edge_ids_into(
    node_id: str,
    storage: StorageBackend,
) -> list[str]:
    """Ids of ``supersession_candidate`` edges pointing at ``node_id``.

    These are cleared when the node is resolved (superseded) — the candidacy has
    been decided.
    """
    edges = await storage.get_edges_to(
        node_id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
    )
    return [edge.id for edge in edges]
