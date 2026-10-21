"""Read-side assembly for visualization snapshots and graph listings.

These build the JSON payloads a client reads (the snapshot and the graph list,
whose shapes `dev-docs/VIZ_CONTRACT.md` specifies), and the node vectors a
layout by meaning needs, from a raw storage backend. They run **inside the
process that owns the storage**, each MCP session answering an RPC from the
hub, so `mem://` and in-memory graphs are readable (the reads execute where the
data lives).

Kept separate from any transport so both the RPC handler (`hub_client.py`) and
tests can call them directly.
"""

from epimemer.core.advisories import WarningPolicy
from epimemer.core.types import DecisionKind, NodeStatus, NodeType, live_edges
from epimemer.pipelines.query.validity import validity_from_edges
from epimemer.pipelines.reflection.boundaries import (
    boundary_proposals_from,
    declined_boundaries_from,
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
    node_type_label,
    relation_label_to_view,
    timeline_to_view,
)
from epimemer.visualization.protocol import VIZ_SCHEMA_VERSION


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

    A proposal a judge declined is left out, read from this graph's own
    journal by the rule `reflect` uses, so the panel draws the endpoint with the
    mark it would otherwise wear rather than a "proposed, review" chip nobody
    will be asked to answer.
    """
    nodes = await storage.viz_list_nodes(graph)
    edges = live_edges(await storage.viz_list_edges(graph))
    timelines = await storage.viz_list_timelines(graph)
    metacontexts = await storage.viz_list_metacontexts(graph)
    relation_labels = await storage.viz_list_relation_labels(graph)
    retired = await storage.viz_list_nodes(graph, historical_status=NodeStatus.HISTORICAL)
    verdicts = await storage.viz_list_decisions(
        graph, kinds=[DecisionKind.BOUNDARY_DECLINED, DecisionKind.REOPENED]
    )
    proposals = boundary_proposals_from(
        succession_holders([*nodes, *retired]),
        edges,
        validity_from_edges(edges),
        declined_boundaries_from(verdicts),
    )
    return {
        "graph": graph,
        "schema_version": VIZ_SCHEMA_VERSION,
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


async def assemble_embeddings(storage: StorageBackend, graph: str, node_type: str | None) -> dict:
    """The vectors of `graph`'s active nodes, of one type when `node_type` names one.

    A viewer that lays topic nodes out by meaning, nearer the more alike they
    are, needs their vectors, and the snapshot does not carry them: 2,000
    vectors would quadruple it, and every snapshot refresh would pay for them
    whether or not anything is laid out. Topic nodes are the usual request, a
    few hundred vectors, so this answers for one type at a time when asked to.

    The nodes are read with the snapshot's own `viz_list_nodes(graph)`, and
    each entry names its node's type in the snapshot's words, so a client joins
    the two by `node_id` with nothing to reconcile. A node with no embedding is
    absent from the list. Entries come in the snapshot's node order, then by
    model. `models` maps each model present to its vector length, so a client
    can pick one model and size its arrays before reading an entry; vectors
    from two different models are never comparable with each other.

    `node_type` is checked here as well as at the hub, because the session is
    what answers, and an unknown type must be an error rather than an empty
    list that reads as a graph with no vectors.
    """
    wanted = NodeType(node_type).value if node_type is not None else None
    labelled = ((node.id, node_type_label(node)) for node in await storage.viz_list_nodes(graph))
    types = {node_id: label for node_id, label in labelled if wanted is None or label == wanted}
    position = {node_id: index for index, node_id in enumerate(types)}
    records = sorted(
        await storage.viz_list_embeddings(graph, item_ids=list(types)),
        key=lambda record: (position[record.item_id], record.model_id),
    )
    models: dict[str, int] = {}
    for record in records:
        models.setdefault(record.model_id, len(record.vector))
    return {
        "graph": graph,
        "schema_version": VIZ_SCHEMA_VERSION,
        "node_type": node_type,
        "models": models,
        "embeddings": [
            {
                "node_id": record.item_id,
                "node_type": types[record.item_id],
                "model_id": record.model_id,
                "vector": list(record.vector),
            }
            for record in records
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
    and a judge (`EVENT_LOG.md` §12.4).

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
