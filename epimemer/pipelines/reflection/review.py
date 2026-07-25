"""Domain helpers for the epistemic review loop (see REVIEW_EPISTEMIC.md).

Pure planning functions (reads only) that compute the edges / edge-ids a
supersession or resolution must apply atomically.
"""

from collections.abc import Awaitable, Callable

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EpistemicNode,
    Inference,
    NodeEdge,
    NodeStatus,
)
from epimemer.storage.protocol import StorageBackend


def _unique(ids: list[str]) -> list[str]:
    """De-duplicate ids while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


async def review_labels(
    node: EpistemicNode,
    storage: StorageBackend,
    *,
    resolve_frames: "FrameResolver | None" = None,
) -> dict[str, list[str]]:
    """Compute epistemic review labels for an active node (REVIEW_EPISTEMIC.md §4.1).

    Edges are the source of truth; this *derives* the labels retrieval surfaces so
    a caller knows a node may be superseded, evidentially stale, or contested —
    without the node ever leaving ACTIVE. Each label maps to the related node ids
    the caller can hop to:

    - ``superseded_candidate`` — node has incoming ``supersession_candidate`` edges
      (newer facts proposed to replace it); ids are those newer facts. (Case A)
    - ``evidence_stale`` (inferences only) — node has incoming
      ``evidence_superseded`` flags and/or ``derived_from`` a fact now SUPERSEDED;
      ids are the changed facts. (Case B)
    - ``contested`` — node has a ``contradiction`` edge (either direction) to a
      node that is still ACTIVE and in the same frame (an unresolved same-frame
      conflict); ids are the contesting nodes.
    """
    labels: dict[str, list[str]] = {}

    candidates = await storage.get_edges_to(
        node.id, edge_type=EdgeType.SUPERSESSION_CANDIDATE
    )
    if candidates:
        labels["superseded_candidate"] = _unique([e.src_id for e in candidates])

    if isinstance(node, Inference):
        stale_sources: list[str] = []
        for edge in await storage.get_edges_to(
            node.id, edge_type=EdgeType.EVIDENCE_SUPERSEDED
        ):
            stale_sources.append(edge.src_id)
        for edge in await storage.get_edges_from(
            node.id, edge_type=EdgeType.DERIVED_FROM
        ):
            evidence = await storage.get_node(edge.dst_id)
            if evidence is not None and evidence.status == NodeStatus.SUPERSEDED:
                stale_sources.append(edge.dst_id)
        if stale_sources:
            labels["evidence_stale"] = _unique(stale_sources)

    contradiction_edges = list(
        await storage.get_edges_from(node.id, edge_type=EdgeType.CONTRADICTION)
    ) + list(await storage.get_edges_to(node.id, edge_type=EdgeType.CONTRADICTION))
    contesting: list[str] = []
    for edge in contradiction_edges:
        other_id = edge.dst_id if edge.src_id == node.id else edge.src_id
        other = await storage.get_node(other_id)
        if other is None or other.status != NodeStatus.ACTIVE:
            continue  # resolved (the partner was retired) — no longer contested
        if await same_frame(node.id, other_id, storage, resolve=resolve_frames):
            contesting.append(other_id)
    if contesting:
        labels["contested"] = _unique(contesting)

    return labels


async def frames_of(node_id: str, storage: StorageBackend) -> set[str]:
    """Metacontext ids a node belongs to, treating untagged as base reality.

    A node with no ``has_metacontext`` edges is implicitly in "The Real"
    (``BASE_METACONTEXT_ID``); a node explicitly tagged with the base id reduces
    to the same single-frame set. Used to decide whether two nodes share a frame
    (and so whether an apparent conflict is genuine — see REVIEW_EPISTEMIC.md §4.3).
    """
    edges = await storage.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT)
    frames = {edge.dst_id for edge in edges}
    return frames or {BASE_METACONTEXT_ID}


def frame_resolver(storage: StorageBackend) -> "FrameResolver":
    """A `frames_of` that answers each node once.

    Frame checks are made per *pair* — of contradiction candidates, of
    contesting nodes — while the nodes involved are drawn from a much smaller
    set, and each uncached lookup is a full edge scan. Without this, a pass that
    compares P pairs over N nodes does O(P) scans instead of O(N).

    The cache is created by the caller and lives for that one pass, so it cannot
    serve a stale frame to a later operation.
    """
    cache: dict[str, set[str]] = {}

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
    """Whether two nodes share at least one metacontext frame.

    Untagged nodes are both in the base frame, so two untagged nodes share a
    frame (a genuine same-frame relationship); nodes in disjoint frames (e.g. a
    fiction frame vs. base reality) do not. A frame overlap means an apparent
    contradiction is real; disjoint frames mean the two simply coexist.

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
    flagged: list[tuple[EpistemicNode, dict[str, list[str]]]] = []
    resolve_frames = frame_resolver(storage)
    for node in await storage.query_nodes():
        labels = await review_labels(node, storage, resolve_frames=resolve_frames)
        if labels:
            flagged.append((node, labels))
    return flagged


async def plan_evidence_stale_edges(
    superseded_fact_id: str,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Edges flagging inferences whose evidence just changed (Case B).

    When a fact is superseded, the inferences that *directly* depend on it become
    suspect. Dependents are the inferences the fact ``supports`` and those that
    are ``derived_from`` it. Returns one ``evidence_superseded`` edge
    (fact → inference) per direct dependent. Direct only — no transitive cascade
    (a dependent that is later superseded flags its own dependents in turn).
    """
    dependent_ids: list[str] = []
    seen: set[str] = set()

    # fact --supports--> inference
    for edge in await storage.get_edges_from(
        superseded_fact_id, edge_type=EdgeType.SUPPORTS
    ):
        node = await storage.get_node(edge.dst_id)
        if isinstance(node, Inference) and node.id not in seen:
            seen.add(node.id)
            dependent_ids.append(node.id)

    # inference --derived_from--> fact
    for edge in await storage.get_edges_to(
        superseded_fact_id, edge_type=EdgeType.DERIVED_FROM
    ):
        node = await storage.get_node(edge.src_id)
        if isinstance(node, Inference) and node.id not in seen:
            seen.add(node.id)
            dependent_ids.append(node.id)

    return [
        NodeEdge(
            src_id=superseded_fact_id,
            dst_id=inference_id,
            type=EdgeType.EVIDENCE_SUPERSEDED,
        )
        for inference_id in dependent_ids
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
