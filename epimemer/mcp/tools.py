"""Core tool implementations for the Epimemer MCP server.

Each function is a pure async function with explicit dependencies —
no global state, easily testable. The MCP server layer in server.py
calls these and wraps the results.
"""

from datetime import datetime

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
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
    ValueSignal,
)
from epimemer.embeddings.protocol import EmbeddingProvider
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
    """Execute a Petri net, optionally emitting visualization events.

    Redirects stdout to stderr during execution because Petritype has
    debug print() statements that would corrupt MCP's stdio transport.
    """
    import sys

    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        if event_bus is not None:
            from epimemer.visualization.instrumented_executor import execute_with_events
            return await execute_with_events(
                graph, event_bus, pipeline_name, max_iterations=max_transitions,
            )
        return await ExecutableGraphOperations.execute_graph(graph, max_transitions=max_transitions)
    finally:
        sys.stdout = original_stdout


# --- Segment (step 1 of agent-driven ingest) ---


async def segment_text(
    content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    config: ServerConfig,
    *,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Segment text and store the document and segments. Returns segments for the agent to decompose.

    This is step 1 of the two-step agent-driven ingest flow. The agent
    receives the segments, extracts topics/facts/inferences itself, then
    calls store_decomposition (step 2).
    """
    from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net
    from epimemer.pipelines.segmentation.semantic_similarity import semantic_similarity_segmentation_net

    strategy = segmentation_strategy or config.segmentation_strategy

    doc = RawDocument(content=content, metadata=metadata or {})
    await storage.store_document(doc)

    if strategy == "semantic":
        seg_graph = semantic_similarity_segmentation_net(doc, embedding_provider)
        seg_graph, _ = await _run_net(seg_graph, "segmentation:semantic", event_bus)
    else:
        seg_graph = paragraph_split_segmentation_net(doc)
        seg_graph, _ = await _run_net(seg_graph, "segmentation:paragraph", event_bus)

    segments: list[Segment] = list(seg_graph.place_named("Segments").tokens)

    for segment in segments:
        await storage.store_segment(segment)

    # Only return IDs and boundaries — the agent already has the original text.
    result = {
        "document_id": doc.id,
        "segments": [
            {"segment_id": s.id, "char_count": len(s.text)}
            for s in segments
        ],
    }
    meta = ResponseMeta(nodes_returned=len(segments))
    return result, meta


# --- Store Decomposition (step 2 of agent-driven ingest) ---


async def store_decomposition(
    document_id: str,
    segments: list[dict],
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    metacontext_id: str | None = None,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Store agent-provided decomposition: topics, facts, inferences per segment.

    Each entry in segments should have:
        segment_id: str
        topics: list[str]
        facts: list[str]
        inferences: list[str]

    Creates typed nodes, edges, and embeddings for everything.
    """
    from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment, edge_creation_net

    total_topics = 0
    total_facts = 0
    total_inferences = 0
    total_edges = 0

    # Accumulate the whole document's writes, then persist them atomically so a
    # mid-document failure cannot leave a partial graph.
    batch_nodes: list[EpistemicNode] = []
    batch_edges: list[NodeEdge] = []
    batch_embeddings: list[EmbeddingRecord] = []

    stored_segments = await storage.get_segments_for_document(document_id)
    segments_by_id = {s.id: s for s in stored_segments}

    for seg_data in segments:
        segment_id = seg_data["segment_id"]
        raw_topics: list[str] = seg_data.get("topics", [])
        raw_facts: list[str] = seg_data.get("facts", [])
        raw_inferences: list[str] = seg_data.get("inferences", [])

        topics = [Topic(content=t, source_id=segment_id, extraction_method="agent") for t in raw_topics]
        facts = [Fact(content=f, source_id=segment_id, extraction_method="agent") for f in raw_facts]
        inferences = [Inference(content=i, source_id=segment_id, extraction_method="agent") for i in raw_inferences]

        segment = segments_by_id.get(segment_id)
        if segment is None:
            raise ValueError(f"Segment '{segment_id}' not found for document '{document_id}'")

        decomposed = DecomposedSegment(
            segment=segment,
            topics=topics,
            facts=facts,
            inferences=inferences,
        )

        # Create edges via Petri net
        edge_graph = edge_creation_net(decomposed)
        edge_graph, _ = await _run_net(edge_graph, "edge_creation", event_bus)
        edges: list[NodeEdge] = list(edge_graph.place_named("Edges").tokens)

        seg_nodes: list[EpistemicNode] = [*topics, *facts, *inferences]
        batch_nodes.extend(seg_nodes)
        batch_edges.extend(edges)

        # Embed all nodes
        if seg_nodes:
            texts = [n.content for n in seg_nodes]
            vectors = await embedding_provider.embed(texts)
            for node, vector in zip(seg_nodes, vectors):
                batch_embeddings.append(EmbeddingRecord(
                    item_id=node.id,
                    model_id=embedding_provider.model_id,
                    vector=vector,
                ))

        # Add metacontext edges if specified
        if metacontext_id and seg_nodes:
            for node in seg_nodes:
                batch_edges.append(NodeEdge(
                    src_id=node.id,
                    dst_id=metacontext_id,
                    type=EdgeType.HAS_METACONTEXT,
                ))
            total_edges += len(seg_nodes)

        total_topics += len(topics)
        total_facts += len(facts)
        total_inferences += len(inferences)
        total_edges += len(edges)

    # One atomic write for the entire document.
    await storage.write_batch_tx(
        nodes=batch_nodes, edges=batch_edges, embeddings=batch_embeddings,
    )

    nodes_created = {
        "topics": total_topics,
        "facts": total_facts,
        "inferences": total_inferences,
    }
    result = {
        "document_id": document_id,
        "nodes_created": nodes_created,
        "edges_created": total_edges,
    }
    meta = ResponseMeta(
        nodes_returned=total_topics + total_facts + total_inferences,
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
    cross_frame: bool = False,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Search the memory graph via hybrid retrieval (vector + graph expansion).

    If metacontext_id is provided, results are frame-scoped to that metacontext
    plus untagged base-reality nodes (set cross_frame=True to ignore frames).
    Metacontext labels and computed review labels (superseded_candidate /
    evidence_stale / contested) are always included on returned nodes.
    """
    from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
    from epimemer.pipelines.query.types import QueryRequest, QueryResult
    from epimemer.pipelines.reflection.review import frames_of, review_labels

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

    # Frame-scoping: when scoped to a metacontext, return that frame plus untagged
    # base-reality nodes (knowledge in The Real applies everywhere); sibling frames
    # are excluded unless cross_frame is set. (REVIEW_EPISTEMIC.md §4.3)
    if metacontext_id and not cross_frame:
        in_frame = []
        for node in nodes:
            node_frames = await frames_of(node.id, storage)
            if metacontext_id in node_frames or BASE_METACONTEXT_ID in node_frames:
                in_frame.append(node)
        nodes = in_frame

    # Build node dicts with metacontext labels and computed review labels.
    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        mc_labels = await _metacontext_labels(node.id, storage)
        if mc_labels:
            node_dict["metacontexts"] = mc_labels
        review = await review_labels(node, storage)
        if review:
            node_dict["review"] = review
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
    embedding_provider: EmbeddingProvider,
) -> tuple[dict, ResponseMeta]:
    """Update a node by creating a new version (supersession).

    The replacement is embedded and inherits the original's edges so it remains
    searchable and connected (see supersede_node).
    """
    from epimemer.pipelines.graph_construction.versioning import supersede_node

    old_node = await storage.get_node(node_id)
    if old_node is None:
        raise ValueError(f"Node '{node_id}' not found")

    # Create new node of the same type, carrying over the value signal so a
    # content correction does not reset reinforcement history. The signal is
    # copied (not shared) so later reinforcement of the new node cannot mutate
    # the superseded original's recorded value.
    carried_value = old_node.value.model_copy()
    if isinstance(old_node, Topic):
        new_node: EpistemicNode = Topic(
            content=new_content, source_id=old_node.source_id, value=carried_value
        )
    elif isinstance(old_node, Fact):
        new_node = Fact(
            content=new_content, source_id=old_node.source_id, value=carried_value
        )
    elif isinstance(old_node, Inference):
        new_node = Inference(
            content=new_content, source_id=old_node.source_id, value=carried_value
        )
    else:
        raise ValueError(f"Unknown node type for node '{node_id}'")

    edge = await supersede_node(old_node, new_node, storage, embedding_provider)

    result = {
        "old_node_id": old_node.id,
        "new_node_id": new_node.id,
        "edge_id": edge.id,
    }
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


async def supersede_by(
    old_id: str,
    existing_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Supersede a node by an already-existing node.

    Use this to resolve an outdated fact or a same-frame contradiction where the
    current truth already exists in the graph (rather than new content). The old
    node is marked superseded (superseded_by → existing); inferences that
    depended on it are flagged evidence_stale; the existing node keeps its own
    edges. Unlike `update`, no new node is created.
    """
    from epimemer.pipelines.graph_construction.versioning import supersede_by_existing

    if old_id == existing_id:
        raise ValueError("A node cannot supersede itself")
    old = await storage.get_node(old_id)
    if old is None:
        raise ValueError(f"Node '{old_id}' not found")
    if await storage.get_node(existing_id) is None:
        raise ValueError(f"Node '{existing_id}' not found")

    edge = await supersede_by_existing(old, existing_id, storage)
    result = {"superseded_id": old_id, "by_id": existing_id, "edge_id": edge.id}
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


# --- Review loop: detection + verdict recording ---


async def check_conflicts(
    fact_ids: list[str],
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    threshold: float = 0.83,
    k: int = 5,
) -> tuple[dict, ResponseMeta]:
    """Find active facts similar to the given facts, for the agent to judge.

    The recall stage of the review loop (REVIEW_EPISTEMIC.md §5.1): for each fact,
    vector-searches active facts above ``threshold`` (excluding the fact itself)
    and returns the candidates with their similarity score, metacontext labels,
    and a same_frame flag. Similarity only *nominates* — the agent then classifies
    each candidate (redundant / supersedes / contradicts / cross-frame /
    compatible) and records the verdict via supersede_by / record_contradiction /
    record_variant. Opt-in and cheap: a single vector lookup per fact at a high bar.
    """
    from epimemer.pipelines.reflection.review import same_frame

    model_id = embedding_provider.model_id
    conflicts: list[dict] = []
    candidate_count = 0

    for fact_id in fact_ids:
        source = await storage.get_node(fact_id)
        if not isinstance(source, Fact):
            continue
        embeddings = await storage.get_embeddings_for_item(fact_id, model_id=model_id)
        if not embeddings:
            continue
        # k + 1 because the fact is its own nearest neighbour; trim back to k.
        hits = await storage.vector_search(
            embeddings[0].vector, model_id, k=k + 1, node_type=NodeType.FACT,
        )
        candidates: list[dict] = []
        for item_id, score in hits:
            if item_id == fact_id or score < threshold:
                continue
            cand = await storage.get_node(item_id)
            if not isinstance(cand, Fact):
                continue
            candidates.append({
                "id": cand.id,
                "content": cand.content,
                "score": round(score, 4),
                "metacontexts": await _metacontext_labels(cand.id, storage),
                "same_frame": await same_frame(fact_id, cand.id, storage),
            })
            if len(candidates) >= k:
                break
        if candidates:
            conflicts.append({
                "fact": {"id": source.id, "content": source.content},
                "candidates": candidates,
            })
            candidate_count += len(candidates)

    result = {"conflicts": conflicts, "threshold": threshold}
    meta = ResponseMeta(nodes_returned=candidate_count)
    return result, meta


async def record_contradiction(
    a_id: str,
    b_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Record a genuine contradiction between two facts (both stay active).

    Creates a single ``contradiction`` edge (idempotent — one per pair, either
    direction). Both facts remain ACTIVE and retrievable; retrieval flags them
    contested so nothing downstream trusts a contested fact blindly. Returns a
    notify_user signal: a same-frame contradiction is epistemically consequential
    and should be surfaced to the user for resolution (REVIEW_EPISTEMIC.md §7). A
    cross-frame pair is *not* a real contradiction — record_variant fits better.
    """
    from epimemer.pipelines.reflection.review import same_frame

    if a_id == b_id:
        raise ValueError("A node cannot contradict itself")
    if await storage.get_node(a_id) is None:
        raise ValueError(f"Node '{a_id}' not found")
    if await storage.get_node(b_id) is None:
        raise ValueError(f"Node '{b_id}' not found")

    shares_frame = await same_frame(a_id, b_id, storage)
    edge_id, created = await _ensure_symmetric_edge(
        a_id, b_id, EdgeType.CONTRADICTION, storage
    )

    result = {
        "edge_id": edge_id,
        "created": created,
        "same_frame": shares_frame,
        "notify_user": shares_frame,
    }
    if not shares_frame:
        result["warning"] = (
            "These facts are in different metacontext frames, so this is not a "
            "genuine contradiction — consider record_variant instead."
        )
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


async def record_variant(
    a_id: str,
    b_id: str,
    storage: StorageBackend,
) -> tuple[dict, ResponseMeta]:
    """Record that two facts are one proposition resolved differently per frame.

    Creates a single ``variant_of`` edge (idempotent — one per pair, either
    direction) so a cross-frame divergence (e.g. base reality vs. a fiction frame)
    is a graph traversal rather than a re-derivation (REVIEW_EPISTEMIC.md §8). Both
    facts stay active. variant_of is for facts in *different* frames; if the two
    share a frame, a same_frame note is returned so the agent can reconsider (a
    same-frame conflict is a contradiction, not a variant).
    """
    from epimemer.pipelines.reflection.review import same_frame

    if a_id == b_id:
        raise ValueError("A node cannot be a variant of itself")
    if await storage.get_node(a_id) is None:
        raise ValueError(f"Node '{a_id}' not found")
    if await storage.get_node(b_id) is None:
        raise ValueError(f"Node '{b_id}' not found")

    shares_frame = await same_frame(a_id, b_id, storage)
    edge_id, created = await _ensure_symmetric_edge(
        a_id, b_id, EdgeType.VARIANT_OF, storage
    )

    result = {"edge_id": edge_id, "created": created, "same_frame": shares_frame}
    if shares_frame:
        result["warning"] = (
            "These facts share a metacontext frame; variant_of is meant for "
            "cross-frame divergence — if they conflict, record_contradiction fits."
        )
    meta = ResponseMeta(nodes_returned=2)
    return result, meta


# --- Reflect (analysis — no LLM) ---


async def reflect(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.85,
    decay_rate: float = 0.05,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Analyse the memory graph and return candidates for the agent to act on.

    Runs embedding-based analysis and value decay (applied immediately).
    Returns split candidates, similar topic pairs, enrichment candidates,
    and contradiction pairs for the agent to review and act on via
    memory.apply_reflection.
    """
    from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
    from epimemer.pipelines.reflection.topic_consolidation import find_similar_topic_pairs
    from epimemer.pipelines.reflection.topic_enrichment import gather_associated_material, _should_enrich
    from epimemer.pipelines.reflection.topic_splitting import should_split
    from epimemer.pipelines.reflection.value_decay import apply_decay

    model_id = embedding_provider.model_id

    # 1. Decay (applied immediately — no agent input needed)
    nodes_decayed = await apply_decay(storage, decay_rate=decay_rate)

    # 2. Find similar topic pairs for consolidation
    pairs = await find_similar_topic_pairs(
        storage, embedding_provider,
        similarity_threshold=similarity_threshold,
        model_id=model_id,
    )
    similar_pairs = [
        {
            "topic_a": {"id": a.id, "content": a.content},
            "topic_b": {"id": b.id, "content": b.content},
            "similarity": round(score, 4),
        }
        for a, b, score in pairs
    ]

    # 3. Find split candidates (topics with high internal variance)
    all_topics = await storage.query_nodes(node_type=NodeType.TOPIC)
    topics = [t for t in all_topics if isinstance(t, Topic)]

    split_candidates = []
    for topic in topics:
        material = await gather_associated_material(topic, storage)
        if len(material) < 4:
            continue
        material_vectors = await embedding_provider.embed(material)
        if should_split(material_vectors):
            split_candidates.append({
                "topic_id": topic.id,
                "topic_content": topic.content,
                "material": material,
            })

    # 4. Find enrichment candidates (thin descriptions with rich material)
    enrichment_candidates = []
    for topic in topics:
        material = await gather_associated_material(topic, storage)
        if _should_enrich(topic, material, material_ratio=3.0):
            enrichment_candidates.append({
                "topic_id": topic.id,
                "current_content": topic.content,
                "associated_material": material,
            })

    # 5. Detect contradictions (safety net for anything ingest-time check missed).
    #    Similarity nominates; keep only same-frame pairs — a high-similarity pair
    #    across disjoint metacontext frames is coexistence, not a contradiction.
    from epimemer.pipelines.reflection.review import gather_pending_review, same_frame

    contradiction_pairs_raw = await detect_contradictions(
        storage, embedding_provider,
        similarity_threshold=0.80,
        model_id=model_id,
    )
    contradictions = []
    for a, b, score in contradiction_pairs_raw:
        if not await same_frame(a.id, b.id, storage):
            continue
        contradictions.append({
            "fact_a": {"id": a.id, "content": a.content},
            "fact_b": {"id": b.id, "content": b.content},
            "similarity": round(score, 4),
        })

    # 6. Surface the pending-review worklist: active nodes already carrying review
    #    state (a candidate to supersede, stale evidence, or an unresolved
    #    contest), with the related ids to act on via apply_reflection /
    #    supersede_by / record_variant.
    pending_review = [
        {
            "node": {"id": n.id, "content": n.content, "node_type": _node_type_key(n)},
            "review": labels,
        }
        for n, labels in await gather_pending_review(storage)
    ]

    result = {
        "nodes_decayed": nodes_decayed,
        "similar_pairs": similar_pairs,
        "split_candidates": split_candidates,
        "enrichment_candidates": enrichment_candidates,
        "contradictions": contradictions,
        "pending_review": pending_review,
    }
    meta = ResponseMeta(
        nodes_returned=(
            len(similar_pairs) + len(split_candidates)
            + len(enrichment_candidates) + len(contradictions)
            + len(pending_review)
        ),
    )
    return result, meta


# --- Apply Reflection (stores agent decisions) ---


async def apply_reflection(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    parents: list[dict] | None = None,
    splits: list[dict] | None = None,
    enrichments: list[dict] | None = None,
    merges: list[dict] | None = None,
    supersessions: list[dict] | None = None,
    merge_similarity_threshold: float = 0.92,
) -> tuple[dict, ResponseMeta]:
    """Apply agent-provided reflection decisions to the graph.

    parents: [{children_ids: [str], content: str}] — synthesized parent topics
    splits: [{topic_id: str, subtopics: [str]}] — split a broad topic
    enrichments: [{topic_id: str, new_content: str}] — improved descriptions
    merges: [{source_ids: [str], content: str}] — fuse near-duplicate topics
        into one combined topic (sources retained as MERGED history). Each merge
        is applied only if *every* pair of sources clears
        merge_similarity_threshold; otherwise it is rejected. This is the one
        consolidation that retires nodes from the active graph, so the bar is
        high by design — use parents for merely related (not duplicate) topics.
    supersessions: [{old_id: str, by_id: str}] — resolve a flagged/contested node
        (from reflect's pending_review) by superseding ``old_id`` with an existing
        node ``by_id``. Atomic: marks old superseded (lineage old → by), flags
        inferences that depended on old as evidence_stale, and clears any
        supersession candidacy on it. The winner is unchanged; no new node.
    """
    from epimemer.pipelines.graph_construction.versioning import (
        merge_nodes,
        plan_subtopic_edges,
        supersede_by_existing,
        supersede_node,
    )
    from epimemer.pipelines.reflection.topic_consolidation import all_pairs_above_threshold

    parents_created = 0
    topics_split = 0
    topics_enriched = 0
    topics_merged = 0
    merges_rejected = 0
    supersessions_applied = 0
    model_id = embedding_provider.model_id

    # 1. Create parent topics for similar groups
    for parent_spec in (parents or []):
        children_ids: list[str] = parent_spec["children_ids"]
        content: str = parent_spec["content"]

        children: list[EpistemicNode] = []
        for cid in children_ids:
            node = await storage.get_node(cid)
            if node is not None:
                children.append(node)

        if len(children) < 2:
            continue

        parent_topic = Topic(
            content=content,
            source_id=children[0].source_id if hasattr(children[0], "source_id") else "",
            extraction_method="agent:parent_synthesis",
            metadata={"synthesized_from": children_ids},
        )
        edges = await plan_subtopic_edges(children, parent_topic.id, storage)
        vectors = await embedding_provider.embed([parent_topic.content])
        await storage.write_batch_tx(
            nodes=[parent_topic],
            edges=edges,
            embeddings=[EmbeddingRecord(
                item_id=parent_topic.id, model_id=model_id, vector=vectors[0]
            )],
        )
        parents_created += 1

    # 2. Split broad topics into subtopics
    for split_spec in (splits or []):
        topic_id: str = split_spec["topic_id"]
        subtopic_contents: list[str] = split_spec["subtopics"]

        parent = await storage.get_node(topic_id)
        if parent is None or not isinstance(parent, Topic):
            continue

        subtopics = [
            Topic(content=sc, source_id=parent.source_id, extraction_method="agent:split", metadata={"split_from": topic_id})
            for sc in subtopic_contents
        ]
        edges = await plan_subtopic_edges(subtopics, parent.id, storage)
        vectors = await embedding_provider.embed([st.content for st in subtopics])
        embeddings = [
            EmbeddingRecord(item_id=st.id, model_id=model_id, vector=vec)
            for st, vec in zip(subtopics, vectors)
        ]
        await storage.write_batch_tx(
            nodes=subtopics, edges=edges, embeddings=embeddings,
        )
        topics_split += 1

    # 3. Enrich topic descriptions
    for enrich_spec in (enrichments or []):
        topic_id = enrich_spec["topic_id"]
        new_content: str = enrich_spec["new_content"]

        old_topic = await storage.get_node(topic_id)
        if old_topic is None or not isinstance(old_topic, Topic):
            continue

        enriched = Topic(
            content=new_content,
            source_id=old_topic.source_id,
            value=old_topic.value,
            extraction_method=f"{old_topic.extraction_method}:enriched",
            metadata={**old_topic.metadata, "enriched_from": topic_id},
        )
        # supersede_node embeds the replacement and migrates edges.
        await supersede_node(old_topic, enriched, storage, embedding_provider)
        topics_enriched += 1

    # 4. Merge near-duplicate topics into one (guarded by a high similarity bar)
    for merge_spec in (merges or []):
        source_ids: list[str] = merge_spec["source_ids"]
        content = merge_spec["content"]

        sources: list[EpistemicNode] = []
        for sid in source_ids:
            node = await storage.get_node(sid)
            if isinstance(node, Topic):
                sources.append(node)

        if len(sources) < 2:
            continue

        # Only collapse genuine duplicates: every pair must clear the bar, or
        # the merge is refused (distinct-but-related topics are left untouched).
        if not await all_pairs_above_threshold(
            sources, storage, model_id, merge_similarity_threshold
        ):
            merges_rejected += 1
            continue

        merged_value = ValueSignal(
            confidence=max(s.value.confidence for s in sources),
            relevance=max(s.value.relevance for s in sources),
            novelty=sum(s.value.novelty for s in sources) / len(sources),
        )
        merged_topic = Topic(
            content=content,
            source_id=sources[0].source_id,
            value=merged_value,
            extraction_method="agent:merge",
            metadata={"merged_from": source_ids},
        )
        await merge_nodes(sources, merged_topic, storage, embedding_provider)
        topics_merged += 1

    # 5. Resolve flagged/contested nodes by superseding the loser with an existing
    #    winner (the resolution action of the review loop). Missing or self-pairs
    #    are skipped rather than raised so a batch partially applies cleanly.
    for supersede_spec in (supersessions or []):
        old_id = supersede_spec["old_id"]
        by_id = supersede_spec["by_id"]
        if old_id == by_id:
            continue
        old_node = await storage.get_node(old_id)
        if old_node is None or await storage.get_node(by_id) is None:
            continue
        await supersede_by_existing(old_node, by_id, storage)
        supersessions_applied += 1

    result = {
        "parents_created": parents_created,
        "topics_split": topics_split,
        "topics_enriched": topics_enriched,
        "topics_merged": topics_merged,
        "merges_rejected": merges_rejected,
        "supersessions_applied": supersessions_applied,
    }
    meta = ResponseMeta(
        nodes_returned=(
            parents_created + topics_split + topics_enriched
            + topics_merged + supersessions_applied
        ),
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
    from epimemer.pipelines.reflection.review import review_labels

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

    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        review = await review_labels(node, storage)
        if review:
            node_dict["review"] = review
        nodes_data.append(node_dict)
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
    """Restore nodes and edges from archive data as one atomic batch.

    The whole archive is reconstructed first (so a malformed record fails before
    anything is written), then persisted in a single ``write_batch_tx`` — all of
    it lands or none of it does, rather than leaving a half-restored graph.
    """
    nodes = [_reconstruct_node(nd) for nd in archive_data.get("nodes", [])]
    edges = [NodeEdge(**ed) for ed in archive_data.get("edges", [])]

    await storage.write_batch_tx(nodes=nodes, edges=edges)

    result = {
        "nodes_restored": len(nodes),
        "edges_restored": len(edges),
    }
    meta = ResponseMeta(nodes_returned=len(nodes))
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


async def _metacontext_labels(node_id: str, storage: StorageBackend) -> list[str]:
    """Content labels of the metacontexts a node is tagged with."""
    edges = await storage.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT)
    labels: list[str] = []
    for edge in edges:
        mc = await storage.get_metacontext(edge.dst_id)
        if mc:
            labels.append(mc.content)
    return labels


async def _symmetric_edge_between(
    a_id: str, b_id: str, edge_type: EdgeType, storage: StorageBackend
) -> NodeEdge | None:
    """An existing edge of ``edge_type`` between a and b, in either direction."""
    for edge in await storage.get_edges_from(a_id, edge_type=edge_type):
        if edge.dst_id == b_id:
            return edge
    for edge in await storage.get_edges_from(b_id, edge_type=edge_type):
        if edge.dst_id == a_id:
            return edge
    return None


async def _ensure_symmetric_edge(
    a_id: str, b_id: str, edge_type: EdgeType, storage: StorageBackend
) -> tuple[str, bool]:
    """Create a symmetric edge between a and b if absent. Returns (edge_id, created).

    Keeps symmetric relationships (contradiction, variant_of) to one edge per
    pair regardless of direction, so repeated recording does not accumulate
    duplicates.
    """
    existing = await _symmetric_edge_between(a_id, b_id, edge_type, storage)
    if existing is not None:
        return existing.id, False
    edge = NodeEdge(src_id=a_id, dst_id=b_id, type=edge_type)
    await storage.store_edge(edge)
    return edge.id, True


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


async def ensure_base_metacontext(storage: StorageBackend) -> Metacontext:
    """Return the reserved base-reality frame ("The Real"), creating it if absent.

    Identified by a fixed reserved id (BASE_METACONTEXT_ID), never by content, so
    it is never confused with a user metacontext whose text happens to mention
    reality. Untagged knowledge is treated as belonging to this frame.
    """
    existing = await storage.get_metacontext(BASE_METACONTEXT_ID)
    if existing is not None:
        return existing
    mc = Metacontext(
        id=BASE_METACONTEXT_ID,
        content="The Real",
        description="Base reality — the default frame for untagged knowledge.",
    )
    await storage.store_metacontext(mc)
    return mc


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


# --- Graph management ---


def _similar_names(target: str, candidates: list[str], max_results: int = 3) -> list[str]:
    """Find candidate names similar to target using edit distance."""
    from difflib import SequenceMatcher

    scored = [
        (name, SequenceMatcher(None, target.lower(), name.lower()).ratio())
        for name in candidates
        if name != target
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [name for name, score in scored[:max_results] if score > 0.4]


async def graph_stats(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Summarize the active graph: node counts by type, edge counts by type, totals.

    Aggregate-only — does not materialize node or edge bodies.
    """
    node_counts = await storage.count_nodes_by_type()
    edge_counts = await storage.count_edges_by_type()
    metacontexts = await storage.query_metacontexts()
    timelines = await storage.query_timelines()

    nodes_by_type = {nt.value: node_counts.get(nt, 0) for nt in NodeType}
    edges_by_type = {et.value: edge_counts.get(et, 0) for et in EdgeType}
    total_nodes = sum(nodes_by_type.values())
    total_edges = sum(edges_by_type.values())

    result = {
        "graph": storage.current_database,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "nodes_by_type": nodes_by_type,
        # Only surface edge types that are actually present, to keep the
        # response readable; the full zero-filled map is available above logic.
        "edges_by_type": {k: v for k, v in edges_by_type.items() if v > 0},
        "metacontexts": len(metacontexts),
        "timelines": len(timelines),
        "empty": total_nodes == 0 and total_edges == 0,
    }
    meta = ResponseMeta(nodes_returned=total_nodes, source_types=nodes_by_type)
    return result, meta


async def list_graphs(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """List available knowledge graphs."""
    databases = await storage.list_databases()
    current = storage.current_database

    result = {
        "graphs": databases,
        "active_graph": current,
    }
    meta = ResponseMeta(nodes_returned=len(databases))
    return result, meta


async def use_graph(
    name: str,
    storage: StorageBackend,
    *,
    confirm: bool = False,
) -> tuple[dict, ResponseMeta]:
    """Switch to a different knowledge graph.

    If the graph doesn't exist and confirm is False, returns a confirmation
    prompt with similar graph names. If confirm is True, creates the graph.
    """
    existing = await storage.list_databases()

    if name in existing:
        await storage.switch_database(name)
        return {
            "status": "switched",
            "active_graph": name,
            "message": f"Switched to graph '{name}'.",
        }, ResponseMeta()

    # Graph doesn't exist
    if not confirm:
        similar = _similar_names(name, existing)
        result: dict = {
            "status": "confirm_create",
            "message": f"Graph '{name}' does not exist.",
            "existing_graphs": existing,
        }
        if similar:
            result["similar_graphs"] = similar
            result["message"] += f" Did you mean one of: {', '.join(similar)}?"
        result["message"] += " Call again with confirm=true to create it."
        return result, ResponseMeta()

    # Create by switching (SurrealDB creates databases on use)
    await storage.switch_database(name)
    return {
        "status": "created",
        "active_graph": name,
        "message": f"Created and switched to new graph '{name}'.",
    }, ResponseMeta()


async def delete_graph(
    name: str,
    storage: StorageBackend,
    *,
    confirm: bool = False,
) -> tuple[dict, ResponseMeta]:
    """Delete a knowledge graph permanently.

    Requires confirm=True. Refuses to delete the currently active graph.
    """
    existing = await storage.list_databases()

    if name not in existing:
        similar = _similar_names(name, existing)
        result: dict = {
            "status": "not_found",
            "message": f"Graph '{name}' does not exist.",
            "existing_graphs": existing,
        }
        if similar:
            result["similar_graphs"] = similar
        return result, ResponseMeta()

    if name == storage.current_database:
        return {
            "status": "refused",
            "message": f"Cannot delete the active graph '{name}'. Switch to a different graph first.",
            "active_graph": name,
        }, ResponseMeta()

    if not confirm:
        return {
            "status": "confirm_delete",
            "message": f"This will permanently delete graph '{name}' and all its data. "
            "Call again with confirm=true to proceed.",
        }, ResponseMeta()

    await storage.delete_database(name)
    return {
        "status": "deleted",
        "message": f"Graph '{name}' has been permanently deleted.",
    }, ResponseMeta()
