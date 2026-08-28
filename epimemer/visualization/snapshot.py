"""Read-side assembly for visualization snapshots and graph listings.

These build the JSON payloads the browser expects (`{"graph", "nodes", "edges"}`
and `{"graphs", "active_graph", "backend"}`) from a raw storage backend. They run
**inside the process that owns the storage** — historically the embedded viz
server, now each MCP session answering an RPC from the hub — so `mem://` and
in-memory graphs are readable (the reads execute where the data lives).

Kept separate from any transport so both the RPC handler (`hub_client.py`) and
tests can call them directly.
"""

from epimemer.storage.protocol import StorageBackend, resolve_reflect_threshold
from epimemer.visualization.events import (
    edge_to_view,
    metacontext_to_view,
    node_to_view,
    relation_label_to_view,
    timeline_to_view,
)


async def assemble_snapshot(storage: StorageBackend, graph: str) -> dict:
    """Full snapshot of `graph` — nodes, edges, timelines, metacontexts.

    Metacontexts ride along because `has_metacontext` edges carry only ids, and
    a frame the viewer cannot name is a frame it cannot offer as a filter.
    Relation labels ride along for the same reason one layer over: an edge
    carries its label as a bare string, so the vocabulary's descriptions live
    nowhere the viewer can reach from the edge alone.
    """
    nodes = await storage.viz_list_nodes(graph)
    edges = await storage.viz_list_edges(graph)
    timelines = await storage.viz_list_timelines(graph)
    metacontexts = await storage.viz_list_metacontexts(graph)
    relation_labels = await storage.viz_list_relation_labels(graph)
    return {
        "graph": graph,
        "nodes": [node_to_view(n, graph).model_dump(mode="json") for n in nodes],
        "edges": [edge_to_view(e, graph).model_dump(mode="json") for e in edges],
        "timelines": [
            timeline_to_view(t, graph).model_dump(mode="json") for t in timelines
        ],
        "metacontexts": [
            metacontext_to_view(m, graph).model_dump(mode="json") for m in metacontexts
        ],
        "relation_labels": [
            relation_label_to_view(rl, graph).model_dump(mode="json")
            for rl in relation_labels
        ],
    }


async def list_graphs_result(
    storage: StorageBackend, default_reflect_threshold: int = 10
) -> dict:
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
