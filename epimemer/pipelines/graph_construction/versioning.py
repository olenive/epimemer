"""Node history and versioning functions.

Handles node supersession (creating a new version) and node merging
(combining multiple nodes into one).
"""

from datetime import datetime, timezone

from pydantic import BaseModel

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    JudgeRef,
    MergeUndo,
    MergedEdge,
    NodeEdge,
    NodeStatus,
    Topic,
    lineage_edge_type_for,
    migration_disposition,
    read_merge_undo,
    with_merge_undo,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.storage.protocol import StorageBackend, resolve_merge_settings


async def supersede_node(
    old_node: EpistemicNode,
    new_node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    status: NodeStatus,
    judge: JudgeRef | None = None,
) -> NodeEdge:
    """Create a new version of a node.

    Marks the old node as superseded, stores and embeds the new node, carries
    its edges over according to `status`, and creates a lineage edge from old to
    new — all atomically (the storage backend applies it as a single
    transaction).

    Embedding and edge migration are part of the operation so that *every*
    supersession path is correct by construction: a replacement that is not
    embedded is invisible to vector search, and one that does not inherit the
    edges it is entitled to is orphaned — from its evidence after a correction,
    or from its frame and topics after a world-change.

    Args:
        old_node: The node being superseded.
        new_node: The replacement node.
        storage: The storage backend.
        embedding_provider: Used to embed the replacement node's content.
        status: Why the old node is being retired — `CORRECTED` (it was wrong)
            or `HISTORICAL` (the world changed, and it is still right of its
            period). No default: the two are opposite events and only the
            caller knows which one happened. It selects two things
            besides the status itself: the edge migration policy — see
            `migration_disposition`, under which a `HISTORICAL` node
            keeps its own provenance while only its frame and tags are copied
            onto the replacement — and which lineage edge is written, see
            `lineage_edge_type_for`. Judgments made about the old node stay on
            it under *either* status, so that is not one of the
            differences.

    Returns:
        The lineage edge linking old to new: `superseded_by` for a correction,
        `temporally_followed_by` for a world-change.
    """
    from epimemer.pipelines.reflection.review import (
        find_candidate_edge_ids_into,
        plan_evidence_stale_edges,
    )

    now = datetime.now(timezone.utc)

    # Sources, tags, and relationships ride along via edge migration below
    # (sourced_from / tagged_with / user edges are migrated, not version-anchored).
    vectors = await embedding_provider.embed([new_node.content])
    new_embedding = EmbeddingRecord(
        item_id=new_node.id,
        model_id=embedding_provider.model_id,
        vector=vectors[0],
    )
    lineage_edge = NodeEdge(
        src_id=old_node.id,
        dst_id=new_node.id,
        type=lineage_edge_type_for(status),
    )
    # Flag dependent inferences (Case B) and clear any candidacy on the old node.
    evidence_edges = await plan_evidence_stale_edges(old_node.id, storage)
    clear_edge_ids = await find_candidate_edge_ids_into(old_node.id, storage)

    await storage.supersede_node_tx(
        old_node, new_node, new_embedding, lineage_edge,
        status=status, superseded_at=now,
        evidence_edges=evidence_edges, clear_edge_ids=clear_edge_ids,
        judge=judge,
    )
    return lineage_edge


async def supersede_by_existing(
    old_node: EpistemicNode,
    existing_id: str,
    storage: StorageBackend,
    *,
    status: NodeStatus,
    judge: JudgeRef | None = None,
) -> NodeEdge:
    """Supersede ``old_node`` by an already-existing node.

    Used where the current truth is a node that already exists (rather than
    freshly-written content) — either because the earlier claim was wrong or
    because the world changed, which ``status`` distinguishes. Marks the
    old node with ``status`` and writes the lineage edge that goes with it
    (old → existing) — `superseded_by` for a correction,
    `temporally_followed_by` for a world-change, see `lineage_edge_type_for`.
    Flags dependent inferences (Case B), and clears any supersession_candidate
    edges on the old node — atomically. The existing node is unchanged: its
    evidence is its own, so the old node's edges are deliberately NOT migrated
    onto it.

    Returns the lineage edge.
    """
    from epimemer.pipelines.reflection.review import (
        find_candidate_edge_ids_into,
        plan_evidence_stale_edges,
    )

    now = datetime.now(timezone.utc)
    lineage_edge = NodeEdge(
        src_id=old_node.id,
        dst_id=existing_id,
        type=lineage_edge_type_for(status),
    )
    evidence_edges = await plan_evidence_stale_edges(old_node.id, storage)
    clear_edge_ids = await find_candidate_edge_ids_into(old_node.id, storage)

    await storage.supersede_by_existing_tx(
        old_node, existing_id, lineage_edge, status=status, superseded_at=now,
        evidence_edges=evidence_edges, clear_edge_ids=clear_edge_ids,
        judge=judge,
    )
    return lineage_edge



async def plan_merge_undo(
    source_nodes: list[EpistemicNode],
    merged_node: EpistemicNode,
    storage: StorageBackend,
    *,
    merged_at: datetime,
) -> MergeUndo:
    """The pre-merge edge partition, captured before migration destroys it.

    **Capture or lose** (REVIEW_MODE.md §7.1). `merge_nodes_tx` re-points every
    migrating edge onto the survivor and collapses duplicates by
    `(src, dst, type)`, and records nothing about which source owned which edge
    — so two sources citing one document leave a single `sourced_from` edge that
    no later pass can split back in two. This is the only moment the partition
    exists.

    Only the edges the merge actually *moves or drops* are captured. Judgment
    edges stay on the source under `migration_disposition`, so recording
    them would have a reversal recreate edges that never left.

    Read before the transaction, the way `merge_nodes_tx` and
    `_plan_copied_edges` already plan theirs: the SurrealDB adapter is
    single-connection and documented as unsafe for concurrent callers, so
    nothing interleaves. Walked in the caller's source order so the payload is
    deterministic.
    """
    source_ids = {node.id for node in source_nodes}

    incident: dict[str, NodeEdge] = {}
    for source in source_nodes:
        for edge in await storage.get_edges_from(source.id):
            incident[edge.id] = edge
        for edge in await storage.get_edges_to(source.id):
            incident[edge.id] = edge

    captured: list[MergedEdge] = []
    for edge in incident.values():
        if migration_disposition(edge.type, NodeStatus.MERGED) == "keep":
            continue
        intra_set = edge.src_id in source_ids and edge.dst_id in source_ids
        captured.append(MergedEdge(
            # For an intra-set edge both endpoints are merging, so `owner_id` is
            # arbitrary and the edge body carries the truth; `src_id` keeps it
            # deterministic.
            owner_id=edge.src_id if edge.src_id in source_ids else edge.dst_id,
            edge=edge.model_dump(exclude={"id"}, mode="json"),
            intra_set=intra_set,
        ))

    return MergeUndo(
        source_ids=[node.id for node in source_nodes],
        edges=captured,
        merged_at=merged_at,
        survivor_content=merged_node.content,
    )


async def evict_deep_merge_undo(
    survivor: EpistemicNode, storage: StorageBackend, *, depth: int,
) -> list[str]:
    """Clear merge payloads more than `depth` levels back along the lineage.

    Depth is a property of the *chain*, not of any one node's list: `A+B→S1`,
    then `S1+C→S2`, and unwinding `S2` to `A, B, C` needs `S2`'s partition and
    `S1`'s. The new survivor is level 1, the survivors it absorbed level 2, and
    so on; anything past `depth` is cleared and becomes permanent.

    **What eviction discards is reversal capability, never a claim.** Every
    merged source, its content, its provenance, its lifecycle episode and its
    `merged_into` edge are untouched. The graph forgets how to replay an edge
    migration automatically. It forgets nothing it knows. `merged_from` is
    deliberately left in place — it is what lets a later refusal tell an evicted
    payload from a node that never had one.

    Returns the ids cleared, which is normally empty: every merge on both real
    graphs to date has been depth 1.
    """
    undo = read_merge_undo(survivor)
    if undo is None:
        return []

    cleared: list[str] = []
    seen = {survivor.id}
    frontier = list(undo.source_ids)
    level = 1
    while frontier:
        level += 1
        ancestors = await storage.get_nodes(frontier)
        next_frontier: list[str] = []
        for node_id in frontier:
            node = ancestors.get(node_id)
            if node is None or node_id in seen:
                continue
            seen.add(node_id)
            ancestor_undo = read_merge_undo(node)
            if ancestor_undo is None:
                continue
            # Read the ancestry out before clearing: the walk has to keep going
            # past an evicted level, since everything above it is deeper still.
            next_frontier.extend(ancestor_undo.source_ids)
            if level > depth:
                await storage.store_node(node.model_copy(
                    update={"metadata": with_merge_undo(node.metadata, None)},
                ))
                cleared.append(node_id)
        frontier = next_frontier
    return cleared


async def merge_nodes(
    source_nodes: list[EpistemicNode],
    merged_node: EpistemicNode,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    undo_depth: int | None = None,
    judge: JudgeRef | None = None,
) -> list[NodeEdge]:
    """Merge multiple nodes into one.

    Stores and embeds the merged node, migrates the sources' relationships onto
    it, marks all sources as merged, and creates merged_into edges from each
    source to the merged node — all atomically (the storage backend applies it
    as a single transaction).

    As with supersede_node, embedding and edge migration are part of the
    operation so the merged node is searchable and inherits its sources'
    supporting evidence rather than being orphaned.

    **The pre-merge edge partition is captured on the survivor** (
    REVIEW_MODE.md §7). Migration re-points every migrating edge and collapses
    duplicates, recording nothing about which source owned which — so the
    partition exists at this moment and at no other, and a merge taken without
    capturing it is permanently irreversible. See `plan_merge_undo`; the bound
    on how far back payloads are kept is `evict_deep_merge_undo`. Nothing reads
    the payload yet: `reverse_merge` is a later step, and this one runs first
    only because its cost rises while it waits.

    **Dependent inferences are flagged, as they are on every other event that
    changes a premise**. A merge is the only one that does not retire the
    claim — the survivor is ACTIVE and carries every source — so it writes
    `evidence_merged` rather than `evidence_superseded`: the wording under the
    inference changed, which is worth a re-read, and nothing was overturned,
    which is what the supersession flag would have said. This is also the only
    chance to record it: the `derived_from` edge migrates onto the survivor in
    the same transaction, so afterwards nothing distinguishes the dependent
    from one drawn on the survivor directly.

    Args:
        source_nodes: The nodes being merged.
        merged_node: The new combined node.
        storage: The storage backend.
        embedding_provider: Used to embed the merged node's content.
        undo_depth: How far back along the lineage merge payloads are kept.
            `None` reads the active graph's setting, which is what every
            production caller wants and none of them can forget: resolving here
            rather than at each call site is why a new merge path cannot ship
            with the bound quietly missing. Pass a number to override it. Must
            be at least 1 — the merge being made now is level 1, and a depth
            below that would discard the payload in the same breath as
            capturing it.

    Returns:
        A list of merged_into edges, one per source node.
    """
    if undo_depth is None:
        undo_depth = (
            resolve_merge_settings(await storage.get_merge_overrides())
        ).undo_depth
    if undo_depth < 1:
        raise ValueError(
            f"undo_depth must be at least 1, got {undo_depth}: the merge being "
            f"made is level 1, so a lower bound would capture the partition and "
            f"discard it in the same call."
        )
    from epimemer.pipelines.reflection.review import plan_evidence_merged_edges

    now = datetime.now(timezone.utc)

    # The merged node inherits its sources' sources/tags/relationships via edge
    # migration below (sourced_from / tagged_with / user edges are migrated).
    # Its **frame** is the exception and is re-stated rather than migrated.
    vectors = await embedding_provider.embed([merged_node.content])
    merged_embedding = EmbeddingRecord(
        item_id=merged_node.id,
        model_id=embedding_provider.model_id,
        vector=vectors[0],
    )
    lineage_edges = [
        NodeEdge(src_id=source.id, dst_id=merged_node.id, type=EdgeType.MERGED_INTO)
        for source in source_nodes
    ]

    # The survivor's frame is **re-stated**, not inherited. Its content is
    # synthesised, so no source's framing was ever made about this wording, and
    # a migrated edge would answer *which world is this about* on the merging
    # agent's behalf while crediting somebody else. `migration_disposition`
    # keeps `has_metacontext` on the sources for the same reason; these are the
    # replacement. The set is well-defined because every merge path refuses
    # sources that do not stand in exactly the same frames, so the union is that
    # one set — and an empty one (an undeclared graph) re-states nothing, which
    # is right: nobody has said anything to restate.
    from epimemer.pipelines.frames import frame_edges
    from epimemer.pipelines.reflection.review import frames_for

    source_frames = await frames_for([source.id for source in source_nodes], storage)
    restated = frame_edges(
        merged_node.id,
        {frame for frames in source_frames.values() for frame in frames},
        judge=judge,
    )

    # Planned per source, so each flag names the wording that went away rather
    # than the survivor that replaced it. An inference resting on two of the
    # sources is told about both.
    #
    # Run for every source kind rather than guarded to facts: a topic has no
    # dependents of this shape, so it plans nothing and costs two reads, while a
    # type guard is one more place inference merge would have to remember to
    # widen.
    evidence_edges = [
        edge
        for source in source_nodes
        for edge in await plan_evidence_merged_edges(source.id, storage)
    ]

    # Captured *before* the transaction, because the transaction is what
    # destroys it (§7.1). Assigned onto the caller's node rather than a copy:
    # `topic_consolidation` returns the node it passed in, so a copy would leave
    # the caller holding a version the store disagrees with.
    merged_node.metadata = with_merge_undo(
        merged_node.metadata,
        await plan_merge_undo(source_nodes, merged_node, storage, merged_at=now),
    )

    await storage.merge_nodes_tx(
        source_nodes, merged_node, merged_embedding,
        [*lineage_edges, *restated], merged_at=now,
        evidence_edges=evidence_edges, judge=judge,
    )

    # After the transaction, so a merge that fails evicts nothing. Eviction is
    # idempotent, so the worst a failure here costs is a payload kept one merge
    # longer than the bound.
    await evict_deep_merge_undo(merged_node, storage, depth=undo_depth)
    return lineage_edges


class ReverseRefused(BaseModel):
    """Why a merge was not reversed, in words the caller can act on.

    Prose rather than a code, matching `MergeRefused` and `BoundaryRefused`: the
    reasons do not form a vocabulary anything branches on, and each is only
    useful to the extent it says which of several near-identical situations
    occurred. The distinction that matters most is between a refusal that will
    never lift (the payload aged out) and one a person can clear (something has
    come to depend on the survivor).
    """

    reason: str


def _repointed(edge_id: str, source_ids: set[str], survivor_id: str) -> str:
    return survivor_id if edge_id in source_ids else edge_id


async def _frames_for_reversal(node_ids, storage):
    """The sources' stated frames, imported late to avoid an import cycle."""
    from epimemer.pipelines.reflection.review import frames_for

    return await frames_for(node_ids, storage)


async def reversal_refusal(
    survivor: EpistemicNode,
    undo: MergeUndo,
    storage: StorageBackend,
) -> ReverseRefused | None:
    """Why this merge must not be reversed, or `None` if nothing objects.

    Reversal ends in a hard delete of the survivor, so **an edge this check does
    not notice is an edge the reversal destroys.** That is why the last rule is
    a set *difference* rather than a list of edge types: a contradiction
    recorded against the survivor after the merge, a `distinct` verdict, a tag,
    a user relation — none is in the payload, none was written by the merge, and
    a contested claim losing its contest record is exactly the loss *nothing is
    destroyed* exists to prevent. An edge type invented next year is refused by
    default rather than deleted by omission.
    """
    if survivor.status is not NodeStatus.ACTIVE:
        # Merged-again is not a separate check: a node with an outgoing
        # `merged_into` is always `MERGED`, so a second condition would be
        # unreachable. It is the common case, though, and it has a next step,
        # so the message says so rather than making the caller work it out.
        merged_again = await storage.get_edges_from(
            survivor.id, edge_type=EdgeType.MERGED_INTO
        )
        if merged_again:
            return ReverseRefused(
                reason=(
                    f"this fact has since been merged into "
                    f"{merged_again[0].dst_id}, which now carries its claim. "
                    f"Reverse that merge first."
                )
            )
        return ReverseRefused(
            reason=(
                f"only an active survivor can be un-merged, and this one is "
                f"{survivor.status.value}. Reversing it would restore sources "
                f"beneath a node the graph has already retired."
            )
        )

    source_ids = set(undo.source_ids)
    expected = {
        (
            _repointed(captured.edge["src_id"], source_ids, survivor.id),
            _repointed(captured.edge["dst_id"], source_ids, survivor.id),
            captured.edge["type"],
        )
        for captured in undo.edges
        if not captured.intra_set
    } | {
        (source_id, survivor.id, EdgeType.MERGED_INTO.value)
        for source_id in source_ids
    } | {
        # The frame the merge **re-stated** on the survivor. It is not in
        # the captured partition, because a merge no longer migrates
        # `has_metacontext` — the sources kept theirs, and these are new edges
        # the merge itself wrote. Left out, every merge became irreversible the
        # moment re-statement shipped: the guard would see the survivor's own
        # frame as something added since. Derived the same way `merge_nodes`
        # derived it, from the sources, so the two cannot drift.
        (survivor.id, frame, EdgeType.HAS_METACONTEXT.value)
        for frames in (
            await _frames_for_reversal(list(source_ids), storage)
        ).values()
        for frame in frames
    }

    incident = {edge.id: edge for edge in await storage.get_edges_from(survivor.id)}
    incident |= {edge.id: edge for edge in await storage.get_edges_to(survivor.id)}
    unexpected = [
        edge for edge in incident.values()
        if (edge.src_id, edge.dst_id, edge.type.value) not in expected
    ]
    if unexpected:
        kinds = ", ".join(sorted({edge.type.value for edge in unexpected}))
        return ReverseRefused(
            reason=(
                f"{len(unexpected)} edges have been added to this fact since "
                f"the merge ({kinds}), and reversing deletes the node they "
                f"point at. Remove or re-point them first if the reversal is "
                f"right — they were judgments made about this wording, and "
                f"nothing here can move them for you."
            )
        )
    return None


async def reverse_merge(
    survivor_id: str,
    storage: StorageBackend,
    *,
    judge: JudgeRef | None = None,
) -> ReverseRefused | dict:
    """Undo one merge: restore the sources, replay their edges, delete the survivor.

    **Reversing returns the graph to the status it had before the merge, and
    reversing back and forth N times is indistinguishable from doing it once**
    (REVIEW_MODE.md §7.6). Every flag the merge set is returned: the sources go
    back to `ACTIVE`, their edges are replayed from the captured partition —
    splitting an edge that collapsed when two sources cited one document — the
    `merged_into` and `evidence_merged` edges the merge wrote are deleted, and
    the survivor is destroyed rather than retired.

    **One boundary, and it is the only place exactness does not hold: status is
    restored, history is appended.** `lifecycle` is append-only by design — a
    node leaving the active set twice is the recurrence the validity model
    legalised — so a
    merge/reverse cycle leaves a closed episode behind. That is the record that
    it happened, which is not a flag and is not returned.

    **No new flag is raised on dependent inferences, and that is the principle
    working rather than an omission.** The merge re-pointed each dependent's
    `derived_from` onto the survivor and flagged it `evidence_merged` — *your
    premise was reworded, go re-read it*. Reversal re-points it back to the
    premise it was actually drawn from, so there is nothing left to re-read, and
    a flag would assert a change the reversal has just undone.

    Returns `ReverseRefused` rather than raising when the graph objects, on the
    same grounds as `merge_refusal`: the caller has real alternatives and can
    only choose one knowing which obstacle it hit. Ids that name nothing raise.
    """
    survivor = await storage.get_node(survivor_id)
    if survivor is None:
        raise ValueError(f"Node '{survivor_id}' not found")

    undo = read_merge_undo(survivor)
    if undo is None:
        # The two absences are different events and the message has to say
        # which: one is a mistake the caller can correct by naming the right
        # node, the other is permanent and no setting brings it back.
        if survivor.metadata.get("merged_from"):
            return ReverseRefused(
                reason=(
                    "this fact was made by a merge, but the record of which "
                    "source held which edge has aged past merge_undo_depth for "
                    "this graph. That is permanent: the partition existed only "
                    "at merge time. The sources are still present and still "
                    "linked by merged_into."
                )
            )
        return ReverseRefused(
            reason=(
                "this fact was not made by a merge, so there is no merge to "
                "reverse. A claim that was wrong is `update`; one the world "
                "moved past is `update` with because='the_world_changed'."
            )
        )

    refusal = await reversal_refusal(survivor, undo, storage)
    if refusal is not None:
        return refusal

    sources = await storage.get_nodes(undo.source_ids)
    missing = [node_id for node_id in undo.source_ids if node_id not in sources]
    if missing:
        return ReverseRefused(
            reason=(
                f"{len(missing)} of the merged sources are no longer in the "
                f"graph, so restoring them is not possible. Reversal puts back "
                f"what the merge retired; it cannot recreate what has since "
                f"been removed."
            )
        )
    source_nodes = [sources[node_id] for node_id in undo.source_ids]

    # Rebuilt rather than `model_copy`d: these are new rows written now, and an
    # edge carrying its old id would collide with nothing but would also claim
    # an identity the graph no longer holds. Everything else — `metadata`,
    # `created_at`, `validity` — is replayed exactly, which is what keeps a
    # judgment's attribution and date intact across the cycle.
    restored_edges = [
        NodeEdge(**captured.edge) for captured in undo.edges
    ]

    incident = {edge.id: edge for edge in await storage.get_edges_from(survivor.id)}
    incident |= {edge.id: edge for edge in await storage.get_edges_to(survivor.id)}
    delete_edge_ids = list(incident)
    # `evidence_merged` is anchored to the sources rather than the survivor, so
    # it is not in the sweep above. A source is retired by the merge that wrote
    # these, and stays retired until this reversal, so every such edge on it
    # belongs to the merge being undone.
    for source in source_nodes:
        for edge in await storage.get_edges_from(
            source.id, edge_type=EdgeType.EVIDENCE_MERGED
        ):
            delete_edge_ids.append(edge.id)

    now = datetime.now(timezone.utc)
    await storage.reverse_merge_tx(
        survivor, source_nodes, restored_edges,
        restored_at=now, delete_edge_ids=delete_edge_ids, judge=judge,
    )
    return {
        "reversed": True,
        "survivor_id": survivor.id,
        # The withdrawn wording, quotable after the node holding it is gone.
        "survivor_content": undo.survivor_content or survivor.content,
        "restored_ids": [node.id for node in source_nodes],
        "edges_restored": len(restored_edges),
        "edges_removed": len(delete_edge_ids),
    }


async def plan_subtopic_edges(
    children: list[Topic],
    parent_id: str,
    storage: StorageBackend,
) -> list[NodeEdge]:
    """Plan SUBTOPIC_OF edges from children to a parent, skipping cycles.

    Pure planning — performs reads (cycle detection) only, no writes. The caller
    persists the parent, edges, and any embeddings together via
    ``write_batch_tx`` so the grouping is atomic. Unlike merge_nodes, children
    remain active (they are not superseded).

    Args:
        children: The child topics to group.
        parent_id: The id of the parent topic the children attach to.
        storage: The storage backend.

    Returns:
        A list of SUBTOPIC_OF edges (child → parent), one per non-cyclic child.
    """
    from epimemer.pipelines.reflection.topic_hierarchy import would_create_cycle

    edges: list[NodeEdge] = []
    for child in children:
        if await would_create_cycle(storage, child.id, parent_id):
            continue
        edges.append(NodeEdge(
            src_id=child.id,
            dst_id=parent_id,
            type=EdgeType.SUBTOPIC_OF,
        ))
    return edges
