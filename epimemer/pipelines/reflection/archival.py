"""Archival functions for the reflection layer.

Identifies superseded and merged nodes that are old enough to archive,
and exports them with their history edges into a serializable format
suitable for cold storage.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel

from epimemer.core.types import (
    NON_KNOWLEDGE_EDGE_TYPES,
    SEGMENT_ANCHOR_EDGE_TYPES,
    EdgeType,
    EpistemicNode,
    Inference,
    NodeStatus,
)
from epimemer.storage.protocol import StorageBackend


async def find_archival_candidates(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
) -> list[EpistemicNode]:
    """Find superseded or merged nodes older than the cutoff.

    Only nodes with status SUPERSEDED or MERGED whose superseded_at
    timestamp is older than max_age_days are returned. Active nodes
    are never included.

    Args:
        storage: The storage backend.
        max_age_days: Minimum age in days since supersession/merge for archival.

    Returns:
        A list of nodes eligible for archival.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    candidates: list[EpistemicNode] = []

    # Check superseded nodes
    superseded = await storage.query_nodes(status=NodeStatus.SUPERSEDED)
    for node in superseded:
        if node.superseded_at is not None and node.superseded_at <= cutoff:
            candidates.append(node)

    # Check merged nodes
    merged = await storage.query_nodes(status=NodeStatus.MERGED)
    for node in merged:
        if node.superseded_at is not None and node.superseded_at <= cutoff:
            candidates.append(node)

    return candidates


async def archive_nodes(
    nodes: list[EpistemicNode],
    storage: StorageBackend,
) -> dict:
    """Export nodes and their history edges to a serializable dict.

    Collects each node's outgoing and incoming edges, and bundles
    them into a dictionary suitable for JSON serialization and
    cold storage. Does NOT delete anything from storage.

    Args:
        nodes: The nodes to archive.
        storage: The storage backend (for edge lookup).

    Returns:
        A dict with 'nodes' (list of node dicts) and 'edges' (list of edge dicts).
    """
    archived_nodes: list[dict] = []
    archived_edges: list[dict] = []
    seen_edge_ids: set[str] = set()

    for node in nodes:
        archived_nodes.append(node.model_dump(mode="json"))

        # Collect all edges connected to this node
        edges_from = await storage.get_edges_from(node.id)
        edges_to = await storage.get_edges_to(node.id)

        for edge in list(edges_from) + list(edges_to):
            if edge.id not in seen_edge_ids:
                seen_edge_ids.add(edge.id)
                archived_edges.append(edge.model_dump(mode="json"))

    return {
        "nodes": archived_nodes,
        "edges": archived_edges,
    }


# --- Nomination: the candidate-generation stage of the hygiene arm ---
#
# Cleanup is one more arm of the review loop (REVIEW_EPISTEMIC.md §12.3), not a
# new subsystem: this plays the same role `check_conflicts` plays for
# contradictions. It is mechanical and cheap — no LLM, no embeddings — because
# its whole job is to hand the agent a short list to judge. Cost tracks the junk,
# not the graph.
#
# Nothing here archives anything. Every nominee is reviewed by the agent and
# approved by a human before `apply_reflection` acts on it.


ArchivalReason = Literal[
    "retired", "evidence_stale", "never_retrieved", "stale_judgment"
]

# Priority order, worst first. A retired node has already been replaced; a stale
# inference has lost its basis; an unused fact is merely unloved. A stale
# judgment is last and is not really an archival claim at all — it asks the
# agent to re-confirm or lower an assessment nobody has revisited, which may
# well end in judging the node back up.
_REASON_ORDER: tuple[ArchivalReason, ...] = (
    "retired", "evidence_stale", "never_retrieved", "stale_judgment",
)

DEFAULT_NOMINATION_LIMIT = 20

# How long an upward judgment protects a node before it is worth re-confirming.
# Longer than the 90-day retirement window on purpose: re-reviewing every judged
# node quarterly is noise, and the point is to catch assessments that have
# quietly expired, not to re-litigate recent ones.
DEFAULT_JUDGMENT_MAX_AGE_DAYS = 180


class ArchivalCandidate(BaseModel):
    """One nominee, with the evidence for nominating it.

    Carries `reason` and `importance` rather than only an id: the agent judges
    these with graph context, and a bare id would make it re-derive why the
    node was proposed at all.
    """
    node_id: str
    node_type: str
    preview: str
    reason: ArchivalReason
    importance: float


def _preview(content: str, limit: int = 120) -> str:
    return content if len(content) <= limit else content[: limit - 1] + "…"


def _candidate(node: EpistemicNode, reason: ArchivalReason) -> ArchivalCandidate:
    return ArchivalCandidate(
        node_id=node.id,
        node_type=type(node).__name__.lower(),
        preview=_preview(node.content),
        reason=reason,
        importance=node.value.importance,
    )


def judgment_is_stale(node: EpistemicNode, cutoff: datetime) -> bool:
    """True when an upward judgment is old enough to be worth re-confirming.

    Reads the *pair* — importance and the clock — rather than the number. An
    unjudged node (`importance_judged_at is None`) is not stale; it was never
    judged, so there is nothing to have expired, and the other nomination
    classes already cover it.

    This is why `importance` has no decay. A decayed importance would be a
    number nobody judged, sitting beside a trail that says otherwise; here the
    recorded assessment stays exactly as recorded and what ages is confidence in
    its currency, which is what a timestamp expresses and a number cannot.
    """
    judged_at = node.value.importance_judged_at
    return judged_at is not None and judged_at <= cutoff


def never_retrieved(node: EpistemicNode) -> bool:
    """True when the node has never been returned by a search.

    Says what it checks. The predicate used to compare `last_reinforced`
    against `created_at` within a second's tolerance and call the result "never
    touched" — which was false twice over: the slack existed only because the
    timestamp defaulted to creation time, and an agent's explicit judgment
    never moved that field at all, so a deliberately-judged node still read as
    untouched.
    """
    return node.value.retrieved_at is None


async def knowledge_in_degree(node_id: str, storage: StorageBackend) -> int:
    """How many nodes depend on this one — its *structural* importance.

    Computed live rather than stored: "new information makes X more important"
    usually arrives as an edge, so the graph already holds the evidence, and a
    cached count goes stale the moment it does.

    Excludes bookkeeping (history/review) edges, which say nothing about
    dependence, and segment anchors, which every extracted node has exactly one
    of — counting those would make the number constant and the test vacuous.
    """
    edges = await storage.get_edges_to(node_id)
    return sum(
        1 for edge in edges
        if edge.type not in NON_KNOWLEDGE_EDGE_TYPES
        and edge.type not in SEGMENT_ANCHOR_EDGE_TYPES
    )


async def evidence_gone(inference: Inference, storage: StorageBackend) -> bool:
    """True when an inference's *entire* evidence set has left the active graph.

    The follow-on to archiving a fact: what was derived from it is now floating.
    It is deliberately all-or-nothing — an inference with one surviving support
    still has a basis — and deliberately only ever *flags*. Inferences are the
    expensive layer to recreate, so they go back through review rather than
    being swept along with their evidence.

    Complements the `evidence_stale` review label, which fires on *any*
    superseded evidence but does not know about archival.
    """
    edges = await storage.get_edges_from(inference.id, edge_type=EdgeType.DERIVED_FROM)
    if not edges:
        return False
    for edge in edges:
        evidence = await storage.get_node(edge.dst_id)
        if evidence is None or evidence.status is NodeStatus.ACTIVE:
            return False
    return True


async def nominate_archival_candidates(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
    importance_ceiling: float = 0.5,
    judgment_max_age_days: int = DEFAULT_JUDGMENT_MAX_AGE_DAYS,
    limit: int = DEFAULT_NOMINATION_LIMIT,
) -> list[ArchivalCandidate]:
    """Propose nodes worth archiving, worst first.

    Three classes, in the priority order §12.3 sets out:

    1. **retired** — SUPERSEDED/MERGED past `max_age_days` and not judged
       important. These are the existing age-based candidates, now value-aware.
    2. **evidence_stale** — active inferences flagged by the review loop, plus
       those whose entire evidence set has since been archived (the follow-on
       to class 1 and 3). Their basis changed; they are the
       expensive-to-recreate layer, so they are flagged rather than swept.
    3. **never_retrieved** — active facts never returned by a search, not judged
       important, and with nothing depending on them.
    4. **stale_judgment** — active nodes held above the ceiling by an upward
       judgment older than `judgment_max_age_days` that nobody has revisited.
       Not an archival claim: importance protects a node from every class above,
       so without this an assessment that has since expired protects it forever
       and the cheap tier never looks at the node again.

    `importance_ceiling` is inclusive of the default (0.5) on purpose: an
    un-judged node is not a node judged worth keeping, and nomination is a
    proposal, not a verdict.
    """
    from epimemer.pipelines.reflection.review import review_labels

    judgment_cutoff = datetime.now(timezone.utc) - timedelta(days=judgment_max_age_days)

    candidates: dict[ArchivalReason, list[ArchivalCandidate]] = {
        reason: [] for reason in _REASON_ORDER
    }

    for node in await find_archival_candidates(storage, max_age_days=max_age_days):
        if node.value.importance <= importance_ceiling:
            candidates["retired"].append(_candidate(node, "retired"))

    for node in await storage.query_nodes(status=NodeStatus.ACTIVE):
        if isinstance(node, Inference):
            labels = await review_labels(node, storage)
            if "evidence_stale" in labels or await evidence_gone(node, storage):
                candidates["evidence_stale"].append(_candidate(node, "evidence_stale"))
            continue
        if node.value.importance > importance_ceiling:
            if judgment_is_stale(node, judgment_cutoff):
                candidates["stale_judgment"].append(_candidate(node, "stale_judgment"))
            continue
        if not never_retrieved(node):
            continue
        if await knowledge_in_degree(node.id, storage) == 0:
            candidates["never_retrieved"].append(_candidate(node, "never_retrieved"))

    ordered = [c for reason in _REASON_ORDER for c in candidates[reason]]
    return ordered[:limit]

