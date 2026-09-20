"""Read-side assembly for visualization snapshots and graph listings.

These build the JSON payloads the browser expects (`{"graph", "nodes", "edges"}`
and `{"graphs", "active_graph", "backend"}`) from a raw storage backend. They run
**inside the process that owns the storage** — historically the embedded viz
server, now each MCP session answering an RPC from the hub — so `mem://` and
in-memory graphs are readable (the reads execute where the data lives).

Kept separate from any transport so both the RPC handler (`hub_client.py`) and
tests can call them directly.
"""

from epimemer.core.advisories import WarningPolicy
from epimemer.core.types import NodeStatus, live_edges
from epimemer.pipelines.query.validity import validity_from_edges
from epimemer.pipelines.reflection.boundaries import (
    boundary_proposals_from,
    succession_holders,
)
from epimemer.storage.protocol import (
    StorageBackend,
    resolve_reflect_threshold,
    warning_settings,
)
from epimemer.visualization.events import (
    boundary_proposal_to_view,
    edge_to_view,
    metacontext_to_view,
    node_to_view,
    relation_label_to_view,
    timeline_to_view,
)


async def assemble_snapshot(storage: StorageBackend, graph: str) -> dict:
    """Full snapshot of `graph`: nodes, edges, timelines, metacontexts and
    relation labels.

    Metacontexts ride along because `has_metacontext` edges carry only ids, and
    a metacontext the viewer cannot name is one it cannot offer as a filter.
    Relation labels ride along for the same reason one layer over: an edge
    carries its label as a bare string, so the vocabulary's descriptions live
    nowhere the viewer can reach from the edge alone.

    Retired edges are left out. The dashboard draws what the graph currently
    says, and a timelink a split or a merge moved would otherwise put the fact
    on two dates at once: the one it was given and the one a judge moved it to.

    Boundary proposals are worked out here rather than read, from these same
    nodes and edges, so the periods drawn and the boundaries offered beside them
    describe one instant. `propose_boundaries` is the wrong call for it: that one
    reads the *active* graph, and this may be a snapshot of another.

    The claims a succession can be about are active or historical, and
    `viz_list_nodes` answers for one status at a time, so the historical ones
    take a second read. It is inside the same guard turn as everything above, so
    it is still one instant.
    """
    nodes = await storage.viz_list_nodes(graph)
    edges = live_edges(await storage.viz_list_edges(graph))
    timelines = await storage.viz_list_timelines(graph)
    metacontexts = await storage.viz_list_metacontexts(graph)
    relation_labels = await storage.viz_list_relation_labels(graph)
    retired = await storage.viz_list_nodes(graph, historical_status=NodeStatus.HISTORICAL)
    proposals = boundary_proposals_from(
        succession_holders([*nodes, *retired]),
        edges,
        validity_from_edges(edges),
    )
    return {
        "graph": graph,
        "nodes": [node_to_view(n, graph).model_dump(mode="json") for n in nodes],
        "edges": [edge_to_view(e, graph).model_dump(mode="json") for e in edges],
        "boundary_proposals": [
            boundary_proposal_to_view(p, graph).model_dump(mode="json") for p in proposals
        ],
        "timelines": [timeline_to_view(t, graph).model_dump(mode="json") for t in timelines],
        "metacontexts": [
            metacontext_to_view(m, graph).model_dump(mode="json") for m in metacontexts
        ],
        "relation_labels": [
            relation_label_to_view(rl, graph).model_dump(mode="json") for rl in relation_labels
        ],
    }


async def list_graphs_result(storage: StorageBackend, default_reflect_threshold: int = 10) -> dict:
    """Available graphs, the active one, the backend kind, and the active
    graph's reflection pressure.

    The pressure is included because events alone only tell a viewer what has
    happened *since it connected* — a browser opened onto a graph already
    sitting at 7 of 10 would show nothing until the next store. This is the
    starting value the `reflect_counter_updated` events then move.

    It describes the **active** graph specifically: the counter is read through
    the session's own connection, which is pointed at that graph.
    """
    count = await storage.get_reflect_counter()
    threshold = resolve_reflect_threshold(
        await storage.get_reflect_threshold_override(), default_reflect_threshold
    )
    return {
        "graphs": await storage.list_databases(),
        "active_graph": storage.current_database,
        "backend": storage.backend_name,
        "reflect": {
            "count": count,
            "threshold": threshold,
            "suggested": count >= threshold,
        },
    }


async def warning_settings_result(storage: StorageBackend, default: WarningPolicy) -> dict:
    """What the active graph does about advisories, in `configure_warnings`'s shape.

    A read and nothing else. The dashboard's panel shows these settings and
    never changes one: a write from the browser would be the first write into a
    graph with no author, and every change today is journalled against a session
    and a judge (`WARNINGS_DASHBOARD.md` §2.4).

    It describes the **active** graph, as the reflection pressure above does,
    because the overrides are read through the session's own connection, which
    is pointed at that graph. The response names the graph, so a panel titled
    from it cannot be titled with one it is not showing.

    The process default arrives as a value, the way `ServerConfig` travels
    everywhere else. A session started without one reports the built-in policy,
    which is the same answer `configure_warnings` gives in that case.
    """
    return warning_settings(
        storage.current_database, await storage.get_warning_overrides(), default
    )
