"""Folding a matched claim's retired versions into the version that replaced it.

The condition under which `HISTORICAL` is reachable by default. A
historical claim and its replacement are near-identical text — *"the city is
called Leningrad"* against *"the city is called Saint Petersburg"* — so both
score near the top of the same search, and a claim with four predecessors fills
half a top-10 with versions of one thing. Without this, turning history on is a
ranking regression dressed as a feature.

The fix uses the edge T2 created rather than a text heuristic: when a retired
node and its successor **both** match, the successor takes the slot and the
retired one attaches to it. A retired node whose successor did not match keeps
its own slot — it matched on its own merits and nothing displaced it.
"""

from collections.abc import Sequence

from epimemer.core.types import (
    EdgeType,
    EpistemicNode,
    SUPERSEDED_STATUSES,
)
from epimemer.storage.protocol import StorageBackend

# The edges that say *what came after this*. `merged_into` is deliberately not
# among them: a merged node's content now lives on the survivor, so it is not
# reachable by search in the first place and has no history to hang anywhere.
LINEAGE_FOLD_EDGE_TYPES = (
    EdgeType.SUPERSEDED_BY,
    EdgeType.TEMPORALLY_FOLLOWED_BY,
)


async def _successors_within(
    node_ids: Sequence[str], matched: set[str], storage: StorageBackend
) -> dict[str, list[str]]:
    """For each id, the matched nodes its lineage edges point at.

    Two batched queries, one per edge type, rather than one untyped query per
    node: the whole seed set is known before any of its edges are read, which is
    the shape batching exists to keep in the read paths.
    """
    out: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge_type in LINEAGE_FOLD_EDGE_TYPES:
        edges = await storage.get_edges_for(
            list(node_ids), direction="from", edge_type=edge_type
        )
        for node_id, node_edges in edges.items():
            out[node_id].extend(
                edge.dst_id for edge in node_edges if edge.dst_id in matched
            )
    return out


async def fold_lineage(
    nodes: Sequence[EpistemicNode],
    storage: StorageBackend,
    *,
    unfoldable: Sequence[str] = (),
) -> tuple[list[EpistemicNode], dict[str, list[EpistemicNode]]]:
    """Ranked nodes with retired versions folded away, and where each one went.

    Returns the surviving order and `{host_id: [folded, …]}`, the folded ones
    keeping their rank order so the caller reads a claim's history newest-first
    as it was scored.

    **Only a retired node folds.** Two ACTIVE nodes joined by a lineage edge are
    two current claims, and one of them is not history — that pairing is
    reachable both through `link`, which writes a lineage edge without flipping a
    status (`REVIEW_EPISTEMIC.md` §6.1), and through `restore`, which brings a
    claim back while leaving the edge that recorded it stepping aside. Folding
    there would hide a live answer, so the rule reads the status rather than the
    edge.

    `unfoldable` names ids that must keep their slot whatever their status —
    retrieval passes the claims provably valid at the moment it was asked about.
    A claim true in 1980 tucked underneath the claim that replaced it in 1991 is
    the asked-for answer hidden beneath the wrong one, which is the same defect
    this function exists to prevent, arriving from the temporal side.

    **The walk is cycle-safe by construction, not by luck.**
    `temporally_followed_by` explicitly permits cycles — Saint Petersburg →
    Petrograd → Leningrad → Saint Petersburg is a legal chain, and a recurrence
    closes one — so a walk that trusted the edges to be acyclic would hang on
    real data rather than on corrupt data.
    """
    protected = set(unfoldable)
    # No retired result, no fold — and no edge query either. Most searches are
    # entirely current, so this is the common path rather than a corner of it.
    if not any(
        node.status in SUPERSEDED_STATUSES and node.id not in protected
        for node in nodes
    ):
        return list(nodes), {}

    matched = {node.id: node for node in nodes}
    rank = {node.id: position for position, node in enumerate(nodes)}
    successors = await _successors_within(list(matched), set(matched), storage)

    def host_for(node_id: str) -> str:
        """The last matched node reachable by following lineage forward.

        Ties break on rank, so two successors of one node resolve to the
        better-scored one and two runs of a search agree. A protected node ends
        the walk: it is keeping its own slot, so nothing may be folded past it
        into something later.

        **A cycle has no last version, so the best-ranked member of it becomes
        the host.** Without that rule two claims pointing at each other each fold
        into the other and both leave the result — the walk terminates and the
        answer still disappears, which is the worse failure of the two.
        """
        path = [node_id]
        current = node_id
        while True:
            onward = sorted(successors[current], key=lambda dst: rank[dst])
            ahead = [dst for dst in onward if dst not in path]
            if ahead:
                current = ahead[0]
                path.append(current)
                if current in protected:
                    return current
                continue
            behind = [dst for dst in onward if dst != current]
            if not behind:
                return current
            cycle = path[path.index(behind[0]) :]
            return min(cycle, key=lambda dst: rank[dst])

    kept: list[EpistemicNode] = []
    lineage: dict[str, list[EpistemicNode]] = {}
    for node in nodes:
        foldable = node.status in SUPERSEDED_STATUSES and node.id not in protected
        host = host_for(node.id) if foldable else node.id
        if host == node.id:
            kept.append(node)
        else:
            lineage.setdefault(host, []).append(node)

    return kept, lineage
