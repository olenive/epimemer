"""Core tool implementations for the Epimemer MCP server.

Each function is a pure async function with explicit dependencies —
no global state, easily testable. The MCP server layer in server.py
calls these and wraps the results.
"""

from datetime import datetime

from epimemer.core.types import (
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.llm.protocol import DecompositionProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.types import ResponseMeta
from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment
from epimemer.storage.protocol import StorageBackend

from petritype.core.executable_graph_components import ExecutableGraph, ExecutableGraphOperations

from epimemer.visualization.event_bus import InProcessEventBus


async def _run_net(
    graph: ExecutableGraph,
    pipeline_name: str,
    event_bus: InProcessEventBus | None,
    *,
    max_transitions: int = 10,
) -> tuple[ExecutableGraph, int]:
    """Execute a Petri net, optionally emitting visualization events."""
    if event_bus is not None:
        from epimemer.visualization.instrumented_executor import execute_with_events
        return await execute_with_events(
            graph, event_bus, pipeline_name, max_iterations=max_transitions,
        )
    return await ExecutableGraphOperations.execute_graph(graph, max_transitions=max_transitions)


# --- Ingest ---


async def ingest(
    content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider: DecompositionProvider,
    config: ServerConfig,
    *,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
    metacontext_id: str | None = None,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Ingest text: segment, decompose, construct graph, persist, embed.

    If metacontext_id is provided, all extracted nodes get HAS_METACONTEXT edges.

    Returns a result dict and response metadata.
    """
    from epimemer.pipelines.decomposition.llm_decomposition import llm_decomposition_net
    from epimemer.pipelines.graph_construction.edge_creation import edge_creation_net
    from epimemer.pipelines.graph_construction.persist import persist_decomposed_segment
    from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net
    from epimemer.pipelines.segmentation.semantic_similarity import semantic_similarity_segmentation_net

    strategy = segmentation_strategy or config.segmentation_strategy

    # 1. Create and store the document
    doc = RawDocument(content=content, metadata=metadata or {})
    await storage.store_document(doc)

    # 2. Segment
    if strategy == "semantic":
        seg_graph = semantic_similarity_segmentation_net(doc, embedding_provider)
        seg_graph, _ = await _run_net(seg_graph, "segmentation:semantic", event_bus)
    else:
        seg_graph = paragraph_split_segmentation_net(doc)
        seg_graph, _ = await _run_net(seg_graph, "segmentation:paragraph", event_bus)

    segments: list[Segment] = list(seg_graph.place_named("Segments").tokens)

    # 3. Decompose each segment and construct graph
    total_topics = 0
    total_facts = 0
    total_inferences = 0
    total_edges = 0
    llm_calls = 0

    for segment in segments:
        # Run decomposition
        decomp_graph = llm_decomposition_net(segment, decomposition_provider)
        decomp_graph, _ = await _run_net(decomp_graph, "decomposition", event_bus)

        topics: list[Topic] = list(decomp_graph.place_named("Topics").tokens)
        facts: list[Fact] = list(decomp_graph.place_named("Facts").tokens)
        inferences: list[Inference] = list(decomp_graph.place_named("Inferences").tokens)
        llm_calls += 3  # extract_topics + extract_facts + extract_inferences

        # Create edges
        decomposed = DecomposedSegment(
            segment=segment,
            topics=topics,
            facts=facts,
            inferences=inferences,
        )
        edge_graph = edge_creation_net(decomposed)
        edge_graph, _ = await _run_net(edge_graph, "edge_creation", event_bus)
        edges: list[NodeEdge] = list(edge_graph.place_named("Edges").tokens)

        # Persist
        await persist_decomposed_segment(decomposed, edges, storage)

        # Embed all nodes
        all_nodes: list[EpistemicNode] = [*topics, *facts, *inferences]
        if all_nodes:
            texts = [n.content for n in all_nodes]
            vectors = await embedding_provider.embed(texts)
            for node, vector in zip(all_nodes, vectors):
                emb = EmbeddingRecord(
                    item_id=node.id,
                    model_id=embedding_provider.model_id,
                    vector=vector,
                )
                await storage.store_embedding(emb)

        # Add metacontext edges if specified
        if metacontext_id and all_nodes:
            for node in all_nodes:
                mc_edge = NodeEdge(
                    src_id=node.id,
                    dst_id=metacontext_id,
                    type=EdgeType.HAS_METACONTEXT,
                )
                await storage.store_edge(mc_edge)
            total_edges += len(all_nodes)

        total_topics += len(topics)
        total_facts += len(facts)
        total_inferences += len(inferences)
        total_edges += len(edges)

    nodes_created = {
        "topics": total_topics,
        "facts": total_facts,
        "inferences": total_inferences,
    }
    result = {
        "document_id": doc.id,
        "segments_created": len(segments),
        "nodes_created": nodes_created,
        "edges_created": total_edges,
    }
    meta = ResponseMeta(
        nodes_returned=total_topics + total_facts + total_inferences,
        llm_calls=llm_calls,
        source_types={k: v for k, v in nodes_created.items() if v > 0},
    )
    return result, meta


# --- Search ---


async def search(
    query: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    k: int = 10,
    node_types: list[str] | None = None,
    graph_hops: int = 1,
    metacontext_id: str | None = None,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Search the memory graph via hybrid retrieval (vector + graph expansion).

    If metacontext_id is provided, results are filtered to nodes with that metacontext.
    Metacontext labels are always included in returned nodes.
    """
    from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
    from epimemer.pipelines.query.types import QueryRequest, QueryResult

    # Map string node types to enums
    nt_enums = None
    if node_types:
        nt_enums = [NodeType(t) for t in node_types]

    request = QueryRequest(
        query_text=query,
        k=k,
        node_types=nt_enums,
        graph_hops=graph_hops,
        model_id=embedding_provider.model_id,
    )
    graph = hybrid_retrieval_net(request, embedding_provider, storage)
    graph, _ = await _run_net(graph, "retrieval", event_bus, max_transitions=3)

    query_result: QueryResult = graph.place_named("QueryResult").tokens[0]

    nodes = query_result.nodes
    edges_data = [e.model_dump(mode="json") for e in query_result.edges]

    # Filter by metacontext if specified
    if metacontext_id:
        matching_node_ids = set()
        for node in nodes:
            node_edges = await storage.get_edges_from(node.id)
            for edge in node_edges:
                if edge.type == EdgeType.HAS_METACONTEXT and edge.dst_id == metacontext_id:
                    matching_node_ids.add(node.id)
                    break
        nodes = [n for n in nodes if n.id in matching_node_ids]

    # Build node dicts with metacontext labels
    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        # Always include metacontext labels
        node_edges = await storage.get_edges_from(node.id)
        mc_ids = [e.dst_id for e in node_edges if e.type == EdgeType.HAS_METACONTEXT]
        mc_labels = []
        for mc_id in mc_ids:
            mc = await storage.get_metacontext(mc_id)
            if mc:
                mc_labels.append(mc.content)
        if mc_labels:
            node_dict["metacontexts"] = mc_labels
        nodes_data.append(node_dict)

    result = {
        "nodes": nodes_data,
        "edges": edges_data,
    }
    meta = ResponseMeta(
        nodes_searched=query_result.metadata.nodes_searched,
        nodes_returned=len(nodes),
        graph_hops=query_result.metadata.graph_hops,
        source_types=query_result.metadata.source_types,
    )
    return result, meta


# --- Link ---


async def link(
    src_id: str,
    dst_id: str,
    edge_type: str,
    storage: StorageBackend,
    *,
    weight: float = 1.0,
    metadata: dict | None = None,
) -> tuple[dict, ResponseMeta]:
    """Create a direct edge between two existing nodes."""
    # Validate edge type
    try:
        et = EdgeType(edge_type)
    except ValueError:
        valid = [e.value for e in EdgeType]
        raise ValueError(f"Invalid edge_type '{edge_type}'. Valid types: {valid}")

    # Verify both nodes exist
    src_node = await storage.get_node(src_id)
    if src_node is None:
        raise ValueError(f"Source node '{src_id}' not found")

    dst_node = await storage.get_node(dst_id)
    if dst_node is None:
        raise ValueError(f"Destination node '{dst_id}' not found")

    edge = NodeEdge(
        src_id=src_id,
        dst_id=dst_id,
        type=et,
        weight=weight,
        metadata=metadata or {},
    )
    await storage.store_edge(edge)

    result = {"edge_id": edge.id}
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


# --- Update ---


async def update(
    node_id: str,
    new_content: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Update a node by creating a new version (supersession)."""
    from epimemer.pipelines.graph_construction.versioning import supersede_node

    old_node = await storage.get_node(node_id)
    if old_node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Create new node of the same type
    if isinstance(old_node, Topic):
        new_node: EpistemicNode = Topic(content=new_content, source_id=old_node.source_id)
    elif isinstance(old_node, Fact):
        new_node = Fact(content=new_content, source_id=old_node.source_id)
    elif isinstance(old_node, Inference):
        new_node = Inference(content=new_content, source_id=old_node.source_id)
    else:
        raise ValueError(f"Unknown node type for node '{node_id}'")

    edge = await supersede_node(old_node, new_node, storage)

    result = {
        "old_node_id": old_node.id,
        "new_node_id": new_node.id,
        "edge_id": edge.id,
    }
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


# --- Reflect ---


async def reflect(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    decomposition_provider=None,
    *,
    similarity_threshold: float = 0.85,
    decay_rate: float = 0.05,
    auto_merge: bool = True,
    hierarchical: bool = True,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Run the reflection pipeline: split, consolidation, decay, enrichment, contradiction detection."""
    from epimemer.pipelines.reflection.reflection_net import (
        ConsolidationResult,
        ContradictionResult,
        DecayResult,
        EnrichmentResult,
        ReflectionRequest,
        SplitResult,
        reflection_net,
    )

    request = ReflectionRequest(
        similarity_threshold=similarity_threshold,
        decay_rate=decay_rate,
        auto_merge=auto_merge,
        hierarchical=hierarchical,
        model_id=embedding_provider.model_id,
    )
    graph = reflection_net(request, storage, embedding_provider, decomposition_provider)
    graph, _ = await _run_net(graph, "reflection", event_bus)

    split: SplitResult = graph.place_named("SplitResult").tokens[0]
    consolidation: ConsolidationResult = graph.place_named("ConsolidationResult").tokens[0]
    decay: DecayResult = graph.place_named("DecayResult").tokens[0]
    enrichment: EnrichmentResult = graph.place_named("EnrichmentResult").tokens[0]
    contradiction: ContradictionResult = graph.place_named("ContradictionResult").tokens[0]

    result = {
        "topics_split": split.topics_split,
        "topics_merged": consolidation.topics_merged,
        "parent_topics_created": len(consolidation.parent_topics_created),
        "pairs_found": consolidation.pairs_found,
        "nodes_decayed": decay.nodes_decayed,
        "topics_enriched": enrichment.topics_enriched,
        "contradictions_found": contradiction.pairs_found,
        "contradiction_pairs": contradiction.contradiction_pairs,
    }
    meta = ResponseMeta(
        nodes_returned=consolidation.topics_merged + contradiction.pairs_found,
    )
    return result, meta


# --- Query Graph ---


async def query_graph(
    node_id: str,
    storage: StorageBackend,
    *,
    hops: int = 1,
    edge_types: list[str] | None = None,
) -> tuple[dict, ResponseMeta]:
    """Traverse the graph from a node, returning the local subgraph."""
    from epimemer.pipelines.query.graph_expansion import expand_via_graph

    seed_node = await storage.get_node(node_id)
    if seed_node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Build exclude set if filtering by edge types (include only those listed)
    exclude_edge_types = None
    if edge_types:
        allowed = {EdgeType(t) for t in edge_types}
        all_types = set(EdgeType)
        exclude_edge_types = all_types - allowed

    nodes, edges = await expand_via_graph(
        seed_nodes=[seed_node],
        storage=storage,
        hops=hops,
        exclude_edge_types=exclude_edge_types,
    )

    nodes_data = [_node_to_dict(n) for n in nodes]
    edges_data = [e.model_dump(mode="json") for e in edges]

    source_types: dict[str, int] = {}
    for node in nodes:
        key = _node_type_key(node)
        source_types[key] = source_types.get(key, 0) + 1

    result = {"nodes": nodes_data, "edges": edges_data}
    meta = ResponseMeta(
        nodes_returned=len(nodes),
        graph_hops=hops,
        source_types=source_types,
    )
    return result, meta


# --- Archive ---


async def archive(
    storage: StorageBackend,
    *,
    max_age_days: int = 90,
) -> tuple[dict, ResponseMeta]:
    """Find and export archival candidates to a serializable format."""
    from epimemer.pipelines.reflection.archival import archive_nodes, find_archival_candidates

    candidates = await find_archival_candidates(storage, max_age_days=max_age_days)
    archive_data = await archive_nodes(candidates, storage)

    result = {
        "nodes_archived": len(candidates),
        "archive_data": archive_data,
    }
    meta = ResponseMeta(nodes_returned=len(candidates))
    return result, meta


# --- Restore ---


async def restore(
    archive_data: dict,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Restore nodes and edges from archive data."""
    nodes_restored = 0
    edges_restored = 0

    for node_dict in archive_data.get("nodes", []):
        node = _reconstruct_node(node_dict)
        await storage.store_node(node)
        nodes_restored += 1

    for edge_dict in archive_data.get("edges", []):
        edge = NodeEdge(**edge_dict)
        await storage.store_edge(edge)
        edges_restored += 1

    result = {
        "nodes_restored": nodes_restored,
        "edges_restored": edges_restored,
    }
    meta = ResponseMeta(nodes_returned=nodes_restored)
    return result, meta


# --- Helpers ---


def _node_to_dict(node: EpistemicNode) -> dict:
    """Serialize a node to dict with its type tag."""
    data = node.model_dump(mode="json")
    data["node_type"] = _node_type_key(node)
    return data


def _node_type_key(node: EpistemicNode) -> str:
    """Get the string key for a node's type."""
    if isinstance(node, Topic):
        return "topic"
    elif isinstance(node, Fact):
        return "fact"
    elif isinstance(node, Inference):
        return "inference"
    return "unknown"


def _reconstruct_node(data: dict) -> EpistemicNode:
    """Reconstruct a typed node from a dict.

    Uses the node_type field if present, otherwise tries each type.
    """
    node_type = data.pop("node_type", None)
    if node_type == "topic":
        return Topic(**data)
    elif node_type == "fact":
        return Fact(**data)
    elif node_type == "inference":
        return Inference(**data)

    # Fallback: try each type
    for cls in (Topic, Fact, Inference):
        try:
            return cls(**data)
        except Exception:
            continue
    raise ValueError(f"Cannot reconstruct node from data: {data}")


# --- Timeline tools ---


async def create_timeline(
    name: str,
    storage: StorageBackend,
    *,
    description: str = "",
) -> tuple[dict, ResponseMeta]:
    """Create a new timeline."""
    tl = Timeline(name=name, description=description)
    await storage.store_timeline(tl)
    result = {"timeline_id": tl.id, "name": tl.name}
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def add_timeline_timepoint(
    timeline_id: str,
    storage: StorageBackend,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    label: str | None = None,
) -> tuple[dict, ResponseMeta]:
    """Add a timepoint to an existing timeline."""
    from epimemer.pipelines.timeline.functions import add_timepoint

    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    tl, tp = add_timepoint(tl, start=start, end=end, label=label)
    await storage.store_timeline(tl)  # overwrite with updated timeline

    result = {
        "timeline_id": tl.id,
        "timepoint_id": tp.id,
        "timepoints_count": len(tl.timepoints),
    }
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def query_timeline(
    timeline_id: str,
    storage: StorageBackend,
    *,
    target: datetime | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    k: int = 5,
) -> tuple[dict, ResponseMeta]:
    """Query timepoints on a timeline (nearest or range)."""
    from epimemer.pipelines.timeline.functions import find_nearest, get_in_range

    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    timepoints: list = []
    if target is not None:
        timepoints = find_nearest(tl, target, k=k)
    elif range_start is not None and range_end is not None:
        timepoints = get_in_range(tl, range_start, range_end)
    else:
        # Return all timepoints
        timepoints = tl.timepoints

    result = {
        "timeline_id": tl.id,
        "timeline_name": tl.name,
        "timepoints": [tp.model_dump(mode="json") for tp in timepoints],
    }
    meta = ResponseMeta(nodes_returned=len(timepoints))
    return result, meta


async def create_timelink(
    node_id: str,
    timeline_id: str,
    timepoint_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Link a node to a specific timepoint on a timeline."""
    # Verify node exists
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Verify timeline and timepoint exist
    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    from epimemer.pipelines.timeline.functions import get_timepoint
    tp = get_timepoint(tl, timepoint_id)
    if tp is None:
        raise ValueError(f"Timepoint '{timepoint_id}' not found on timeline '{timeline_id}'")

    edge = NodeEdge(
        src_id=node_id,
        dst_id=timeline_id,
        type=EdgeType.TIMELINK,
        metadata={"timepoint_id": timepoint_id},
    )
    await storage.store_edge(edge)

    result = {"edge_id": edge.id, "timepoint_id": timepoint_id}
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


# --- Metacontext tools ---


async def create_metacontext(
    content: str,
    storage: StorageBackend,
    *,
    description: str = "",
) -> tuple[dict, ResponseMeta]:
    """Create a new metacontext."""
    mc = Metacontext(content=content, description=description)
    await storage.store_metacontext(mc)
    result = {"metacontext_id": mc.id, "content": mc.content}
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def get_metacontexts_for_node(
    node_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Get all metacontexts associated with a node."""
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    edges = await storage.get_edges_from(node_id)
    mc_edges = [e for e in edges if e.type == EdgeType.HAS_METACONTEXT]

    metacontexts = []
    for edge in mc_edges:
        mc = await storage.get_metacontext(edge.dst_id)
        if mc:
            metacontexts.append(mc.model_dump(mode="json"))

    result = {"node_id": node_id, "metacontexts": metacontexts}
    meta = ResponseMeta(nodes_returned=len(metacontexts))
    return result, meta
