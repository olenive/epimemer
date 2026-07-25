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
from epimemer.visualization.events import edge_to_view, node_to_view


async def assemble_snapshot(storage: StorageBackend, graph: str) -> dict:
    """Full node+edge snapshot of `graph`, shaped for the frontend."""
    nodes = await storage.viz_list_nodes(graph)
    edges = await storage.viz_list_edges(graph)
    return {
        "graph": graph,
        "nodes": [node_to_view(n, graph).model_dump(mode="json") for n in nodes],
        "edges": [edge_to_view(e, graph).model_dump(mode="json") for e in edges],
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
