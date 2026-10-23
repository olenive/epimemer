"""Archival functions for the reflection layer.

Nominates nodes for archival (`nominate_archival_candidates`: retired, evidence
stale, never retrieved, and stale judgment), and exports nodes with their
history edges into a serializable format suitable for cold storage.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
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
from epimemer.pipelines.reflection.deferral import (
    DeferralState,
    deferral_due,
    deferral_settings_for,
    deferral_wait,
    deferrals_for,
    rounds_waited,
)
from epimemer.storage.protocol import DeferralSettings, StorageBackend


async def find_archival_candidates(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
) -> list[EpistemicNode]:
    """Find retired nodes old enough to export, excluding the historical ones.

    Nodes retired as CORRECTED or MERGED (plus legacy SUPERSEDED) whose
    `superseded_at` is older than `max_age_days`. Active nodes are never
    included, and neither are HISTORICAL ones: those were retired because the
    world changed, not because they were wrong, so they remain true of their
    period and ageing is not a reason to discard them.

    Args:
        storage: The storage backend.
        max_age_days: Minimum age in days since supersession/merge for archival.

    Returns:
        A list of nodes eligible for archival.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

    candidates: list[EpistemicNode] = []

    # Superseded nodes, but *not* the historical ones. A node retired because
    # the world changed was never wrong — it is still true of its period — so
    # retiring it again for age would be the same defect one level down: the
    # graph discarding something true because it is no longer current.
    # `SUPERSEDED` is the legacy status, kept because older rows do not record
    # which kind they were; they stay eligible, as they were before.
    for status in (NodeStatus.SUPERSEDED, NodeStatus.CORRECTED):
        for node in await storage.query_nodes(status=status):
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
    node_ids = [node.id for node in nodes]
    outgoing = await storage.get_edges_for(node_ids, direction="from")
    incoming = await storage.get_edges_for(node_ids, direction="to")

    archived_edges: list[dict] = []
    seen_edge_ids: set[str] = set()
    for node_id in node_ids:
        for edge in list(outgoing[node_id]) + list(incoming[node_id]):
            if edge.id not in seen_edge_ids:
                seen_edge_ids.add(edge.id)
                archived_edges.append(edge.model_dump(mode="json"))

    return {
        "nodes": [node.model_dump(mode="json") for node in nodes],
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


ArchivalReason = Literal["retired", "evidence_stale", "never_retrieved", "stale_judgment"]

# Priority order, worst first. A retired node has already been replaced; a stale
# inference has lost its basis; an unused fact is merely unloved. A stale
# judgment is last and is not really an archival claim at all — it asks the
# agent to re-confirm or lower an assessment nobody has revisited, which may
# well end in judging the node back up.
_REASON_ORDER: tuple[ArchivalReason, ...] = (
    "retired",
    "evidence_stale",
    "never_retrieved",
    "stale_judgment",
)

DEFAULT_NOMINATION_LIMIT = 20

# The importance at or below which a node is not protected from nomination.
# Named once because the tool that accepts a deferral asks the nominator's own
# question, and a second literal is how the two would come to disagree.
DEFAULT_IMPORTANCE_CEILING = 0.5

# How long an upward judgment protects a node before it is worth re-confirming.
# Longer than the 90-day retirement window on purpose: re-reviewing every judged
# node quarterly is noise, and the point is to catch assessments that have
# quietly expired, not to re-litigate recent ones.
DEFAULT_JUDGMENT_MAX_AGE_DAYS = 180


class DeferredNote(BaseModel):
    """That this nominee was put off before, and what was said about it.

    The judge's prose and the numbers together: `count` deferrals so far,
    `last_round` the reflect round of the latest, `rounds_waited` since then,
    and `next_wait` the rounds another deferral now would hold it back for.
    """

    count: int
    last_round: int
    rounds_waited: int
    because: str
    judged_by: str | None = None
    next_wait: int


class ArchivalCandidate(BaseModel):
    """One nominee, with the evidence for nominating it.

    Carries `reason` and `importance` rather than only an id: the agent judges
    these with graph context, and a bare id would make it re-derive why the
    node was proposed at all.

    `deferred` is set only on a node somebody deferred before, and
    `candidate_payload` leaves the key out otherwise, on the `reopened`
    precedent: *never deferred* and *deferred, reason unrecorded* stay
    distinguishable.
    """

    node_id: str
    node_type: str
    preview: str
    reason: ArchivalReason
    importance: float
    deferred: DeferredNote | None = None


def candidate_payload(candidate: ArchivalCandidate) -> dict:
    """One nominee as the response carries it, with `deferred` absent where unset."""
    payload = candidate.model_dump(mode="json")
    if candidate.deferred is None:
        payload.pop("deferred")
    return payload


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


def never_retrieved_exclusion(
    node: EpistemicNode,
    *,
    in_degree: int,
    retained: dict[str, set[str]],
    deferral: DeferralState | None,
    current_round: int,
    settings: DeferralSettings,
    importance_ceiling: float = DEFAULT_IMPORTANCE_CEILING,
) -> str | None:
    """Why this node is not a `never_retrieved` nominee right now, or None if it is.

    **The one statement of the class**, read by the nominator and by the
    `apply_reflection` step that accepts a deferral. A deferral is only an
    answer to this nomination, so the tool has to ask exactly the question the
    nominator asked; two copies of the test is how a node would come to be
    deferred that reflect never offered, or offered one the tool then refused.

    Takes what it needs already read (`in_degree`, the standing retentions, the
    node's deferral state) so the nominator keeps its batched reads. The
    reasons are phrased for an agent told why its deferral was refused.
    """
    from epimemer.pipelines.reflection.retention import retention_covers

    if node.status is not NodeStatus.ACTIVE:
        return "not active, so not nominated"
    if isinstance(node, Inference):
        return (
            "an inference, which is nominated as evidence_stale rather than "
            "never_retrieved and is answered with `retained`"
        )
    if node.value.importance > importance_ceiling:
        return f"judged above the importance ceiling ({importance_ceiling}), so not nominated"
    if not never_retrieved(node):
        return "a search has returned it, so it is no longer a never_retrieved nominee"
    if in_degree > 0:
        return "something links to it, so it is no longer a never_retrieved nominee"
    if retention_covers(node.id, (), retained):
        return "a standing `retained` verdict keeps it; `reopen` withdraws that first"
    if deferral is not None and not deferral_due(deferral, current_round, settings):
        remaining = deferral_wait(deferral.count, settings) - (current_round - deferral.last_round)
        return (
            f"already deferred, and not offered again for {remaining} more "
            f"reflect round(s); `reopen` asks for it now"
        )
    return None


async def knowledge_in_degree_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, int]:
    """How many nodes depend on each of these — their *structural* importance.

    Computed live rather than stored: "new information makes X more important"
    usually arrives as an edge, so the graph already holds the evidence, and a
    cached count goes stale the moment it does.

    Excludes bookkeeping (history/review) edges, which say nothing about
    dependence, and segment anchors, which every extracted node has exactly one
    of — counting those would make the number constant and the test vacuous.
    """
    incoming = await storage.get_edges_for(node_ids, direction="to")
    return {
        node_id: sum(
            1
            for edge in edges
            if edge.type not in NON_KNOWLEDGE_EDGE_TYPES
            and edge.type not in SEGMENT_ANCHOR_EDGE_TYPES
        )
        for node_id, edges in incoming.items()
    }


async def evidence_gone_for(
    inferences: Sequence[Inference], storage: StorageBackend
) -> dict[str, list[str]]:
    """The archived evidence of inferences that have lost *all* of it.

    **Returns the ids rather than a flag**, and the difference is load-bearing
    rather than cosmetic: a keep verdict anchors to the reasons it covers, so a
    nomination that names none can only be confirmed permanently. Archival is
    the designed-reversible act — `restore` undoes it — so the cycle that bites
    is restore, then re-archive: the inference re-enters this set carrying a
    self-anchor from a verdict that never saw the second archival, and stays
    wrongly silent. Naming the facts makes the second archival a reason nothing
    covers.

    The follow-on to archiving a fact: what was derived from it is now floating.
    It is deliberately all-or-nothing — an inference with one surviving support
    still has a basis — and deliberately only ever *flags*. Inferences are the
    expensive layer to recreate, so they go back through review rather than
    being swept along with their evidence.

    Complements the `evidence_stale` review label, which fires on *any*
    superseded evidence but does not know about archival.

    The evidence nodes are the *neighbours* of the set rather than the set, so
    there is no id list to batch on until the edges come back — which is why
    they are read in a second pass rather than a first. Reading them one at a
    time gave up the early exit below in exchange for a round-trip per edge;
    now the whole neighbourhood arrives at once and the exit is free.
    """
    derived_from = await storage.get_edges_for(
        [inference.id for inference in inferences],
        direction="from",
        edge_type=EdgeType.DERIVED_FROM,
    )
    evidence_by_id = await storage.get_nodes(
        [edge.dst_id for edges in derived_from.values() for edge in edges]
    )

    gone: dict[str, list[str]] = {}
    for inference in inferences:
        edges = derived_from[inference.id]
        archived: list[str] = []
        for edge in edges:
            evidence = evidence_by_id.get(edge.dst_id)
            if evidence is None or evidence.status is NodeStatus.ACTIVE:
                archived = []
                break
            archived.append(edge.dst_id)
        gone[inference.id] = archived
    return gone


async def nominate_archival_candidates(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
    importance_ceiling: float = DEFAULT_IMPORTANCE_CEILING,
    judgment_max_age_days: int = DEFAULT_JUDGMENT_MAX_AGE_DAYS,
    limit: int = DEFAULT_NOMINATION_LIMIT,
    deferral_defaults: DeferralSettings | None = None,
) -> list[ArchivalCandidate]:
    """Propose nodes worth archiving, worst first.

    Four classes, in the priority order §12.3 sets out:

    1. **retired** — SUPERSEDED/MERGED past `max_age_days` and not judged
       important. These are the existing age-based candidates, now value-aware.
    2. **evidence_stale** — active inferences flagged by the review loop, plus
       those whose entire evidence set has since been archived (the follow-on
       to class 1 and 3). Their basis changed; they are the
       expensive-to-recreate layer, so they are flagged rather than swept.
       **`evidence_merged` is deliberately not read here**: a premise that
       absorbed another claim gained provenance rather than losing its basis, so
       nominating on it would have every merge propose discarding its own
       dependents. That label asks the agent for a re-read, which is recorded
       as a keep anchored to the absorbed ids; `archival_reasons` is the
       narrower set this class reads.
    3. **never_retrieved** — active facts and topics never returned by a
       search, not judged important, and with nothing depending on them, less
       any a standing deferral is still holding back
       (`never_retrieved_exclusion`). A deferred node whose wait is over comes
       back first within the class, carrying `deferred`, so the limit cannot
       keep pushing it out behind nodes nobody has looked at yet.
    4. **stale_judgment** — active nodes held above the ceiling by an upward
       judgment older than `judgment_max_age_days` that nobody has revisited.
       Not an archival claim: importance protects a node from every class above,
       so without this an assessment that has since expired protects it forever
       and the cheap tier never looks at the node again.

    `importance_ceiling` is inclusive of the default (0.5) on purpose: an
    un-judged node is not a node judged worth keeping, and nomination is a
    proposal, not a verdict.
    """
    from epimemer.pipelines.reflection.retention import (
        archival_reasons,
        confirmed_reasons_for,
        retention_covers,
    )
    from epimemer.pipelines.reflection.review import review_labels_for

    judgment_cutoff = datetime.now(UTC) - timedelta(days=judgment_max_age_days)

    candidates: dict[ArchivalReason, list[ArchivalCandidate]] = {
        reason: [] for reason in _REASON_ORDER
    }

    for node in await find_archival_candidates(storage, max_age_days=max_age_days):
        if node.value.importance <= importance_ceiling:
            candidates["retired"].append(_candidate(node, "retired"))

    active = list(await storage.query_nodes(status=NodeStatus.ACTIVE))

    # Each class needs edges for a different subset, so the subsets are decided
    # first and read in bulk after. Reading per node instead is what made this
    # scan cost a round-trip per active node.
    inferences = [node for node in active if isinstance(node, Inference)]
    labels_by_node = await review_labels_for(inferences, storage)
    gone_by_node = await evidence_gone_for(inferences, storage)

    unretrieved = [
        node
        for node in active
        if not isinstance(node, Inference)
        and node.value.importance <= importance_ceiling
        and never_retrieved(node)
    ]
    in_degree = await knowledge_in_degree_for([n.id for n in unretrieved], storage)

    # What somebody has already re-read and kept. Read for both classes at once
    # rather than per class: it is one query, and the two differ only in what
    # they hand `retention_covers` as the reasons to check against.
    retained = await confirmed_reasons_for(
        [node.id for node in inferences] + [node.id for node in unretrieved], storage
    )
    deferred = await deferrals_for([node.id for node in unretrieved], storage)
    current_round = await storage.get_reflect_round()
    settings = await deferral_settings_for(storage, deferral_defaults)

    # Deferred nodes whose wait is over, kept apart so they lead their class.
    due_again: list[ArchivalCandidate] = []

    for node in active:
        if isinstance(node, Inference):
            reasons = archival_reasons(labels_by_node.get(node.id, {}), gone_by_node[node.id])
            if reasons and not retention_covers(node.id, reasons, retained):
                candidates["evidence_stale"].append(_candidate(node, "evidence_stale"))
            continue
        if node.value.importance > importance_ceiling:
            if judgment_is_stale(node, judgment_cutoff):
                candidates["stale_judgment"].append(_candidate(node, "stale_judgment"))
            continue
        if not never_retrieved(node):
            continue
        state = deferred.get(node.id)
        excluded = never_retrieved_exclusion(
            node,
            in_degree=in_degree[node.id],
            retained=retained,
            deferral=state,
            current_round=current_round,
            settings=settings,
            importance_ceiling=importance_ceiling,
        )
        if excluded is not None:
            continue
        candidate = _candidate(node, "never_retrieved")
        if state is None:
            candidates["never_retrieved"].append(candidate)
            continue
        candidate.deferred = DeferredNote(
            count=state.count,
            last_round=state.last_round,
            rounds_waited=rounds_waited(state, current_round),
            because=state.because,
            judged_by=state.judged_by,
            next_wait=deferral_wait(state.count + 1, settings),
        )
        due_again.append(candidate)
    candidates["never_retrieved"] = due_again + candidates["never_retrieved"]

    ordered = [c for reason in _REASON_ORDER for c in candidates[reason]]
    return ordered[:limit]
