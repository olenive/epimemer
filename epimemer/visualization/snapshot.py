"""Read-side assembly for visualization snapshots and graph listings.

These build the JSON payloads the browser expects (`{"graph", "nodes", "edges"}`
and `{"graphs", "active_graph", "backend"}`) from a raw storage backend. They run
**inside the process that owns the storage** — historically the embedded viz
server, now each MCP session answering an RPC from the hub — so `mem://` and
in-memory graphs are readable (the reads execute where the data lives).

Kept separate from any transport so both the RPC handler (`hub_client.py`) and
tests can call them directly.
"""

from epimemer.storage.protocol import StorageBackend
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


async def list_graphs_result(storage: StorageBackend) -> dict:
    """Available graphs, the active one, and the backend kind for this session."""
    return {
        "graphs": await storage.list_databases(),
        "active_graph": storage.current_database,
        "backend": storage.backend_name,
    }
