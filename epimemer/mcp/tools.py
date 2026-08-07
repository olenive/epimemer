"""Core tool implementations for the Epimemer MCP server.

Each function is a pure async function with explicit dependencies —
no global state, easily testable. The MCP server layer in server.py
calls these and wraps the results.
"""

import asyncio

from datetime import datetime, timezone
from typing import Sequence

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeChangeEvent,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
    ValueSignal,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.mcp.config import (
    DEFAULT_IMPORTANCE_STEP,
    DEFAULT_REINFORCEMENT_BOOST,
    ServerConfig,
)
from epimemer.mcp.types import ResponseMeta
from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment
from epimemer.storage.protocol import (
    StorageBackend,
    resolve_reflect_threshold,
    validate_graph_name,
)

from petritype.core.executable_graph_components import ExecutableGraph
from petritype.runtime import RunContext, Runner

from epimemer.visualization.event_bus import InProcessEventBus


async def _run_net(
    graph: ExecutableGraph,
    pipeline_name: str,
    event_bus: InProcessEventBus | None,
) -> tuple[ExecutableGraph, int]:
    """Execute a Petri net to quiescence, optionally emitting visualization events.

    Runs until nothing is enabled. There is deliberately no transition budget:
    a net that has stopped firing is finished, and a number chosen in advance is
    either too small — truncating the pipeline and returning a partial result
    with no error — or large enough never to matter.

    Both paths are the same runner; the event bus only adds an observer, so
    watching a pipeline cannot change what it computes.

    Nothing here may write to stdout: MCP's stdio transport is stdout, so a
    stray print corrupts the protocol. The engine's own progress prints are
    gated behind `verbose`, which the runner leaves off, so no suppression is
    needed — and suppressing it by swapping `sys.stdout` would be worse than the
    problem, since that is process-global state mutated across `await` points.
    """
    if event_bus is not None:
        from epimemer.visualization.instrumented_executor import execute_with_events
        return await execute_with_events(graph, event_bus, pipeline_name)

    steps_before = graph.step_count
    graph = await Runner.run_to_completion(RunContext(graph=graph))
    return graph, graph.step_count - steps_before


# --- Segment (step 1 of agent-driven ingest) ---


async def segment_text(
    content: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    config: ServerConfig,
    *,
    source: str | None = None,
    source_type: str | None = None,
    published_by: str | None = None,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Segment text and store the document and segments. Returns segments for the agent to decompose.

    This is step 1 of the two-step agent-driven ingest flow. The agent
    receives the segments, extracts topics/facts/inferences itself, then
    calls store_decomposition (step 2).

    source/source_type describe the originating document; every node decomposed
    from it gets a `sourced_from` edge to this document. `published_by` names a
    publishing/authoring entity — resolved-or-created as an entity Topic and linked
    to the document by a `published_by` (attribution) edge.
    """
    from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net
    from epimemer.pipelines.segmentation.semantic_similarity import semantic_similarity_segmentation_net

    strategy = segmentation_strategy or config.segmentation_strategy

    doc = RawDocument(
        content=content, source=source, source_type=source_type, metadata=metadata or {},
    )
    await storage.store_document(doc)

    if published_by:
        entity = await _upsert_entity_topic(published_by, storage, embedding_provider)
        await storage.store_edge(NodeEdge(
            src_id=doc.id, dst_id=entity.id, type=EdgeType.RELATED,
            label="published_by", kind="attribution",
        ))

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


async def _upsert_entity_topic(
    name: str,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    extraction_method: str = "agent:source",
) -> Topic:
    """Resolve-or-create (by exact name) an entity Topic and persist it directly.

    Used for source/publisher entities at segment time. Exact-name match means a
    repeated name reuses one node; fuzzy duplicates are merged later by reflect.
    """
    existing = await storage.get_node_by_content(name, node_type=NodeType.TOPIC)
    if isinstance(existing, Topic):
        return existing
    topic = Topic(content=name, source_id=None, extraction_method=extraction_method)
    await storage.store_node(topic)
    vec = (await embedding_provider.embed([name]))[0]
    await storage.store_embedding(EmbeddingRecord(
        item_id=topic.id, model_id=embedding_provider.model_id, vector=vec,
    ))
    return topic


def _decomposition_entry(entry) -> tuple[str, list[str], float | None]:
    """Unpack a decomposition entry: a bare content string, or
    `{"content": str, "tags": [name, ...], "importance": float}`.

    `importance` is a *prior*, not a verdict: triviality is only visible once
    the neighbourhood exists, so the real judgment happens at reflect time.
    Omitting it leaves the node at the `ValueSignal` default.
    """
    if isinstance(entry, dict):
        return (
            entry["content"],
            list(entry.get("tags", [])),
            entry.get("importance"),
        )
    return entry, [], None


async def store_decomposition(
    document_id: str,
    segments: list[dict],
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    metacontext_id: str | None = None,
    tags: list[str] | None = None,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Store agent-provided decomposition: topics, facts, inferences per segment.

    Each entry in segments should have:
        segment_id: str
        topics/facts/inferences: each a content string, or {"content": str,
            "tags": [name, ...], "importance": float} for per-node tags and an
            optional importance prior (default 0.5).

    Every node gets a `sourced_from` edge to the originating document. `tags`
    (document-level) and per-node tags are resolved-or-created (by exact name) as
    Topics linked by `tagged_with` edges, so a repeated tag reuses one Topic.
    Everything is persisted in one atomic write.
    """
    from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment, edge_creation_net

    # Accumulate the whole document's writes, then persist them atomically so a
    # mid-document failure cannot leave a partial graph.
    batch_nodes: list[EpistemicNode] = []
    batch_edges: list[NodeEdge] = []
    batch_embeddings: list[EmbeddingRecord] = []

    stored_segments = await storage.get_segments_for_document(document_id)
    segments_by_id = {s.id: s for s in stored_segments}

    total_topics = total_facts = total_inferences = 0
    doc_tag_names = list(tags or [])
    tag_cache: dict[str, Topic] = {}

    async def _tag_topic(name: str) -> Topic:
        """Resolve-or-create a tag Topic, adding new ones to the batch."""
        if name in tag_cache:
            return tag_cache[name]
        existing = await storage.get_node_by_content(name, node_type=NodeType.TOPIC)
        if isinstance(existing, Topic):
            tag_cache[name] = existing
            return existing
        topic = Topic(content=name, source_id=None, extraction_method="agent:tag")
        tag_cache[name] = topic
        batch_nodes.append(topic)
        vec = (await embedding_provider.embed([name]))[0]
        batch_embeddings.append(EmbeddingRecord(
            item_id=topic.id, model_id=embedding_provider.model_id, vector=vec,
        ))
        return topic

    for seg_data in segments:
        segment_id = seg_data["segment_id"]
        segment = segments_by_id.get(segment_id)
        if segment is None:
            raise ValueError(f"Segment '{segment_id}' not found for document '{document_id}'")

        topics: list[Topic] = []
        facts: list[Fact] = []
        inferences: list[Inference] = []
        tag_assignments: list[tuple[EpistemicNode, list[str]]] = []
        for cls, entries, bucket in (
            (Topic, seg_data.get("topics", []), topics),
            (Fact, seg_data.get("facts", []), facts),
            (Inference, seg_data.get("inferences", []), inferences),
        ):
            for entry in entries:
                content, node_tag_names, importance = _decomposition_entry(entry)
                value = (
                    ValueSignal() if importance is None
                    else ValueSignal(importance=importance)
                )
                node = cls(
                    content=content, source_id=segment_id,
                    value=value, extraction_method="agent",
                )
                bucket.append(node)
                names = doc_tag_names + node_tag_names
                if names:
                    tag_assignments.append((node, names))

        decomposed = DecomposedSegment(
            segment=segment, topics=topics, facts=facts, inferences=inferences,
        )
        edge_graph = edge_creation_net(decomposed)
        edge_graph, _ = await _run_net(edge_graph, "edge_creation", event_bus)
        edges: list[NodeEdge] = list(edge_graph.place_named("Edges").tokens)

        seg_nodes: list[EpistemicNode] = [*topics, *facts, *inferences]
        batch_nodes.extend(seg_nodes)
        batch_edges.extend(edges)

        if seg_nodes:
            vectors = await embedding_provider.embed([n.content for n in seg_nodes])
            for node, vector in zip(seg_nodes, vectors):
                batch_embeddings.append(EmbeddingRecord(
                    item_id=node.id, model_id=embedding_provider.model_id, vector=vector,
                ))

        # Provenance: every node is sourced_from the originating document.
        for node in seg_nodes:
            batch_edges.append(NodeEdge(
                src_id=node.id, dst_id=document_id, type=EdgeType.SOURCED_FROM,
            ))
        # Tags: each becomes (or reuses) a Topic linked by tagged_with.
        for node, names in tag_assignments:
            for name in names:
                topic = await _tag_topic(name)
                batch_edges.append(NodeEdge(
                    src_id=node.id, dst_id=topic.id, type=EdgeType.TAGGED_WITH,
                ))
        # Optional metacontext framing.
        if metacontext_id:
            for node in seg_nodes:
                batch_edges.append(NodeEdge(
                    src_id=node.id, dst_id=metacontext_id, type=EdgeType.HAS_METACONTEXT,
                ))

        total_topics += len(topics)
        total_facts += len(facts)
        total_inferences += len(inferences)

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
        "edges_created": len(batch_edges),
    }
    meta = ResponseMeta(
        nodes_returned=total_topics + total_facts + total_inferences,
        source_types={k: v for k, v in nodes_created.items() if v > 0},
    )
    return result, meta



# --- Search ---


# Frame-scoped search over-fetches. Vector top-k is computed before the frame
# filter runs, so a frame whose nodes rank below k would be dropped before the
# filter ever saw them — the query comes back short, or empty. We pull a multiple
# of k candidates and grow the fetch until k in-frame nodes survive or the vector
# store is exhausted. A storage-level frame filter is the eventual answer; this
# bounds the work until then. (Issue 13, REVIEW_EPISTEMIC.md §4.3.)
_FRAME_SCOPE_OVERFETCH = 4
_FRAME_SCOPE_MAX_K = 200


async def _run_retrieval(
    request,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
    event_bus: InProcessEventBus | None,
):
    """Run the hybrid-retrieval net once and return its QueryResult."""
    from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
    from epimemer.pipelines.query.types import QueryResult

    graph = hybrid_retrieval_net(request, embedding_provider, storage)
    graph, _ = await _run_net(graph, "retrieval", event_bus)
    result: QueryResult = graph.place_named("QueryResult").tokens[0]
    return result


async def _in_frame_nodes(
    nodes: list[EpistemicNode], metacontext_id: str, storage: StorageBackend
) -> list[EpistemicNode]:
    """Nodes in `metacontext_id` or in untagged base reality (The Real).

    Knowledge in the base frame applies everywhere; sibling frames are excluded.
    `frames_of` is a storage round-trip per node, so the lookups are gathered
    rather than awaited one at a time.
    """
    from epimemer.pipelines.reflection.review import frames_of

    frame_sets = await asyncio.gather(
        *(frames_of(node.id, storage) for node in nodes)
    )
    return [
        node
        for node, frames in zip(nodes, frame_sets)
        if metacontext_id in frames or BASE_METACONTEXT_ID in frames
    ]


async def _retrieve_frame_scoped(
    request,
    embedding_provider: EmbeddingProvider,
    storage: StorageBackend,
    metacontext_id: str,
    event_bus: InProcessEventBus | None,
) -> tuple[list[EpistemicNode], object]:
    """Retrieve in-frame nodes without being capped by the vector top-k.

    Over-fetch candidates and grow the fetch until at least `request.k` in-frame
    nodes survive the filter, or the store returns fewer hits than asked for
    (exhausted), or the cap is hit. Returns the filtered nodes plus the final
    QueryResult, whose edges and metadata describe the run that produced them.
    """
    k = request.k
    fetch_k = min(k * _FRAME_SCOPE_OVERFETCH, _FRAME_SCOPE_MAX_K)
    while True:
        widened = request.model_copy(update={"k": fetch_k})
        result = await _run_retrieval(widened, embedding_provider, storage, event_bus)
        in_frame = await _in_frame_nodes(result.nodes, metacontext_id, storage)

        exhausted = result.metadata.nodes_searched < fetch_k
        if len(in_frame) >= k or exhausted or fetch_k >= _FRAME_SCOPE_MAX_K:
            return in_frame, result
        fetch_k = min(fetch_k * 2, _FRAME_SCOPE_MAX_K)


_HIERARCHY_PREVIEW_CHARS = 100


def _content_preview(node: EpistemicNode) -> dict:
    """Reduce a node to id plus truncated content.

    Hierarchy responses carry previews and never full material: the point of
    drill-down is that the caller decides what is worth loading, which a
    response that already inlined everything would defeat.
    """
    content = node.content
    if len(content) > _HIERARCHY_PREVIEW_CHARS:
        content = content[:_HIERARCHY_PREVIEW_CHARS] + "…"
    return {"id": node.id, "content_preview": content}


async def _hierarchy_annotations(
    nodes: Sequence[EpistemicNode], storage: StorageBackend
) -> dict[str, dict]:
    """Map topic id -> {parents?, subtopics?} for the Topics among `nodes`.

    Splitting a broad topic builds a SUBTOPIC_OF DAG; without this, retrieval
    never mentions it and a split buys the caller nothing. Only Topics
    participate, and a topic outside any hierarchy gets no keys at all rather
    than empty ones.

    Edge lookups are per topic (the same N+1 family as the rest of `search`
    enrichment), but neighbour bodies are fetched once each across the whole
    result set and reuse nodes the result already carries — so a parent and its
    children coming back together costs no extra fetches.
    """
    topics = [n for n in nodes if isinstance(n, Topic)]
    if not topics:
        return {}

    neighbours_by_topic: dict[str, tuple[list[str], list[str]]] = {}
    needed: set[str] = set()
    for topic in topics:
        parent_edges = await storage.get_edges_from(
            topic.id, edge_type=EdgeType.SUBTOPIC_OF
        )
        child_edges = await storage.get_edges_to(
            topic.id, edge_type=EdgeType.SUBTOPIC_OF
        )
        parent_ids = [e.dst_id for e in parent_edges]
        child_ids = [e.src_id for e in child_edges]
        neighbours_by_topic[topic.id] = (parent_ids, child_ids)
        needed.update(parent_ids)
        needed.update(child_ids)

    known: dict[str, EpistemicNode] = {n.id: n for n in nodes}
    for node_id in needed - known.keys():
        neighbour = await storage.get_node(node_id)
        if neighbour is not None:
            known[node_id] = neighbour

    annotations: dict[str, dict] = {}
    for topic_id, (parent_ids, child_ids) in neighbours_by_topic.items():
        annotation: dict = {}
        parents = [known[i] for i in parent_ids if i in known]
        children = [known[i] for i in child_ids if i in known]
        if parents:
            annotation["parents"] = [_content_preview(p) for p in parents]
        if children:
            annotation["subtopics"] = [_content_preview(c) for c in children]
        if annotation:
            annotations[topic_id] = annotation
    return annotations


async def topic_tree(
    topic_id: str,
    storage: StorageBackend,
    *,
    depth: int = 2,
) -> tuple[dict, ResponseMeta]:
    """Ancestors and a depth-limited subtree for one topic, previews only.

    The drill-down primitive for a split hierarchy: it answers "what is under
    this topic, and what is it part of" with shape and identity rather than
    material, so the caller can pick a branch and fetch only that.

    `depth` counts levels of descendants — 1 is direct subtopics only. A node
    held back by the limit that does have children is flagged ``has_more``, so a
    truncated branch is never mistaken for a leaf.
    """
    from epimemer.pipelines.reflection.topic_hierarchy import (
        get_ancestors,
        get_children,
    )

    if depth < 1:
        raise ValueError("depth must be at least 1")

    node = await storage.get_node(topic_id)
    if node is None:
        raise ValueError(f"Topic {topic_id} not found")
    if not isinstance(node, Topic):
        raise ValueError(f"Node {topic_id} is not a Topic")

    # Shared across the recursion so a DAG with several paths to the same
    # subtopic reports it once, and a malformed cyclic graph still terminates.
    visited: set[str] = {topic_id}

    async def descend(node_id: str, remaining: int) -> list[dict]:
        entries: list[dict] = []
        for child in await get_children(storage, node_id):
            if child.id in visited:
                continue
            visited.add(child.id)
            entry = _content_preview(child)
            if remaining > 1:
                entry["subtopics"] = await descend(child.id, remaining - 1)
            else:
                entry["subtopics"] = []
                if await get_children(storage, child.id):
                    entry["has_more"] = True
            entries.append(entry)
        return entries

    ancestors = await get_ancestors(storage, topic_id)
    subtopics = await descend(topic_id, depth)

    result = {
        "topic": _content_preview(node),
        "ancestors": [_content_preview(a) for a in ancestors],
        "subtopics": subtopics,
        "depth": depth,
    }
    meta = ResponseMeta(
        nodes_returned=len(visited) + len(ancestors),
        source_types={"topic": len(visited) + len(ancestors)},
    )
    return result, meta


def reinforced_signal(value: ValueSignal, boost: float, at: datetime) -> ValueSignal:
    """The value signal a node carries after being retrieved.

    `relevance += boost × (1 − relevance)`: asymptotic, so a node hit
    repeatedly approaches 1.0 instead of pinning there on the second hit.
    Every other field is carried through unchanged — reinforcement records
    *use*, and must not quietly restate a judgment held elsewhere in the
    signal.
    """
    return value.model_copy(
        update={
            "relevance": value.relevance + boost * (1.0 - value.relevance),
            "last_reinforced": at,
        }
    )


async def _reinforce_retrieved(
    nodes: Sequence[EpistemicNode], storage: StorageBackend, boost: float
) -> None:
    """Write retrieval reinforcement back for every node search returned.

    This is the only automatic upward path on `relevance`; without it the
    signal is a monotone function of age and says nothing about whether a node
    is load-bearing — which is exactly the distinction archival candidacy
    needs.

    It deliberately does **not** feed ranking: results stay ordered by
    similarity. The loop (retrieved → more relevant → retrieved) is benign at
    archival granularity and compounds at ranking granularity, where popular
    nodes would crowd out better matches. See
    `dev-docs/REVIEW_EPISTEMIC.md` §12.4.
    """
    if boost <= 0.0:
        return
    at = datetime.now(timezone.utc)
    for node in nodes:
        node.value = reinforced_signal(node.value, boost, at)
        # No backend shares object identity with its callers, so the mutation
        # above is local until it is written back.
        await storage.store_node(node)


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
    reinforcement_boost: float = DEFAULT_REINFORCEMENT_BOOST,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Search the memory graph via hybrid retrieval (vector + graph expansion).

    If metacontext_id is provided, results are frame-scoped to that metacontext
    plus untagged base-reality nodes (set cross_frame=True to ignore frames).
    Frame-scoping over-fetches so an in-frame node ranked below the vector top-k
    is still found (see `_retrieve_frame_scoped`). Metacontext labels and computed
    review labels (superseded_candidate / evidence_stale / contested) are always
    included on returned nodes. Returned Topics that sit in a split hierarchy also
    carry `parents` / `subtopics` as id + preview, so the caller can drill via
    `topic_tree` instead of being handed the whole subtree.

    Returned nodes are reinforced (`reinforcement_boost`, 0.0 disables): being
    retrieved is what tells relevance apart from age. Ranking is unaffected —
    see `_reinforce_retrieved`.
    """
    from epimemer.pipelines.query.types import QueryRequest
    from epimemer.pipelines.reflection.review import review_labels

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

    if metacontext_id and not cross_frame:
        nodes, query_result = await _retrieve_frame_scoped(
            request, embedding_provider, storage, metacontext_id, event_bus
        )
    else:
        query_result = await _run_retrieval(
            request, embedding_provider, storage, event_bus
        )
        nodes = query_result.nodes

    edges_data = [e.model_dump(mode="json") for e in query_result.edges]

    # Reinforce before serializing, so the caller sees the signal the node now
    # holds rather than the one it held a moment ago.
    await _reinforce_retrieved(nodes, storage, reinforcement_boost)

    # Build node dicts with metacontext labels, computed review labels, and —
    # for topics in a split hierarchy — their neighbours, so the caller can
    # drill rather than be handed the whole subtree.
    hierarchy = await _hierarchy_annotations(nodes, storage)
    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        mc_labels = await _metacontext_labels(node.id, storage)
        if mc_labels:
            node_dict["metacontexts"] = mc_labels
        review = await review_labels(node, storage)
        if review:
            node_dict["review"] = review
        node_dict.update(hierarchy.get(node.id, {}))
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


# --- Temporal queries ---


def events_in_window(
    node: EpistemicNode, start: datetime, end: datetime,
) -> list[NodeChangeEvent]:
    """Lifecycle events on a node that fall in the half-open window [start, end).

    Emits `created` when the node was born in the window and `superseded`/`merged`
    (mirroring the node's terminal status) when it was retired in the window. A
    node both born and retired inside one window yields two events.
    """
    events: list[NodeChangeEvent] = []
    if start <= node.created_at < end:
        events.append(NodeChangeEvent(kind="created", at=node.created_at))
    if node.superseded_at is not None and start <= node.superseded_at < end:
        kind = "merged" if node.status == NodeStatus.MERGED else "superseded"
        events.append(NodeChangeEvent(kind=kind, at=node.superseded_at))
    return events


async def as_of(
    at: datetime,
    storage: StorageBackend,
    *,
    node_types: list[str] | None = None,
) -> tuple[dict, ResponseMeta]:
    """Snapshot the active knowledge set as it stood at instant `at`.

    Returns the nodes that had been created by `at` and were not yet retired then
    (the storage `at_time` temporal filter). This is a node-lifecycle snapshot
    only: edges, metacontext, and review labels are *not* time-versioned, so they
    are intentionally omitted — they would reflect the present graph, not the
    graph at `at`.
    """
    nt_enums = [NodeType(t) for t in node_types] if node_types else [None]
    nodes: list[EpistemicNode] = []
    for nt in nt_enums:
        nodes.extend(await storage.query_nodes(at_time=at, node_type=nt))

    source_types: dict[str, int] = {}
    for node in nodes:
        key = _node_type_key(node)
        source_types[key] = source_types.get(key, 0) + 1

    result = {
        "at": at.isoformat(),
        "nodes": [_node_to_dict(n) for n in nodes],
    }
    meta = ResponseMeta(nodes_returned=len(nodes), source_types=source_types)
    return result, meta


async def query_changes(
    windows: list[tuple[datetime, datetime]],
    storage: StorageBackend,
    *,
    node_types: list[str] | None = None,
) -> tuple[dict, ResponseMeta]:
    """What changed (births + retirements) in one or more time windows.

    For each half-open window [start, end), returns the nodes whose creation or
    retirement fell inside it, each tagged with the specific lifecycle event(s)
    and enriched with metacontext + review labels (these are current nodes, so
    present-state labels are accurate). Results are grouped per window; a node
    that changed in several windows appears in each.
    """
    from epimemer.pipelines.reflection.review import review_labels

    nt_enums = [NodeType(t) for t in node_types] if node_types else [None]

    windows_data = []
    total = 0
    source_types: dict[str, int] = {}
    for start, end in windows:
        seen: dict[str, EpistemicNode] = {}
        for nt in nt_enums:
            for node in await storage.query_changes(start=start, end=end, node_type=nt):
                seen[node.id] = node

        changes = []
        for node in seen.values():
            node_dict = _node_to_dict(node)
            node_dict["events"] = [
                e.model_dump(mode="json") for e in events_in_window(node, start, end)
            ]
            mc_labels = await _metacontext_labels(node.id, storage)
            if mc_labels:
                node_dict["metacontexts"] = mc_labels
            review = await review_labels(node, storage)
            if review:
                node_dict["review"] = review
            changes.append(node_dict)

            key = _node_type_key(node)
            source_types[key] = source_types.get(key, 0) + 1
            total += 1

        windows_data.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "changes": changes,
        })

    result = {"windows": windows_data}
    meta = ResponseMeta(nodes_returned=total, source_types=source_types)
    return result, meta


# --- Source / topic / relation queries ---


async def _resolve_hub_id(value: str, storage: StorageBackend) -> str:
    """Resolve a hub reference to an id: a node id, a Topic name, or a document's
    source name (e.g. "ISSUES.md"). Falls back to the raw value if none match.
    """
    if await storage.get_node(value) is not None:
        return value
    topic = await storage.get_node_by_content(value, node_type=NodeType.TOPIC)
    if isinstance(topic, Topic):
        return topic.id
    doc = await storage.get_document_by_source(value)
    if doc is not None:
        return doc.id
    return value


async def find_nodes(
    storage: StorageBackend,
    *,
    sourced_from: str | None = None,
    tagged_with: str | None = None,
    node_types: list[str] | None = None,
    status: str = "active",
    limit: int = 50,
) -> tuple[dict, ResponseMeta]:
    """Find nodes connected to a source or topic hub by graph traversal.

    `sourced_from` (a document/entity id or name) returns the nodes with a
    `sourced_from` edge to it — "which nodes came from X". `tagged_with` (a Topic
    id or name) returns the nodes tagged with that concept. A native graph query,
    replacing the old string-filter listing.
    """
    if tagged_with is not None:
        hub_id = await _resolve_hub_id(tagged_with, storage)
        edge_type = EdgeType.TAGGED_WITH
    elif sourced_from is not None:
        hub_id = await _resolve_hub_id(sourced_from, storage)
        edge_type = EdgeType.SOURCED_FROM
    else:
        raise ValueError("find_nodes requires sourced_from or tagged_with")

    st = NodeStatus(status)
    allowed = set(node_types) if node_types else None

    nodes: list[EpistemicNode] = []
    seen: set[str] = set()
    for edge in await storage.get_edges_to(hub_id, edge_type=edge_type):
        if edge.src_id in seen:
            continue
        seen.add(edge.src_id)
        node = await storage.get_node(edge.src_id)
        if node is None or node.status != st:
            continue
        if allowed and _node_type_key(node) not in allowed:
            continue
        nodes.append(node)
        if len(nodes) >= limit:
            break

    source_types: dict[str, int] = {}
    for node in nodes:
        key = _node_type_key(node)
        source_types[key] = source_types.get(key, 0) + 1
    result = {"nodes": [_node_to_dict(n) for n in nodes]}
    meta = ResponseMeta(nodes_returned=len(nodes), source_types=source_types)
    return result, meta


async def list_sources(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Distinct source/origin nodes with how many nodes reference each — the
    documents nodes are `sourced_from`, plus entities linked by attribution edges
    (e.g. published_by). Discovery before find_nodes."""
    counts: dict[str, int] = {}
    for node in await storage.query_nodes():
        for e in await storage.get_edges_from(node.id, edge_type=EdgeType.SOURCED_FROM):
            counts[e.dst_id] = counts.get(e.dst_id, 0) + 1
        for e in await storage.get_edges_to(node.id):
            if e.type == EdgeType.RELATED and e.kind == "attribution":
                counts[node.id] = counts.get(node.id, 0) + 1

    sources = []
    for dst_id, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        doc = await storage.get_document(dst_id)
        node = await storage.get_node(dst_id)
        name = (doc.source if doc and doc.source else None) or (
            node.content if node else dst_id
        )
        kind = "document" if doc else ("entity" if node else "unknown")
        sources.append({"id": dst_id, "name": name, "kind": kind, "node_count": count})

    result = {"sources": sources}
    meta = ResponseMeta(nodes_returned=len(sources))
    return result, meta


async def list_relations(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Distinct user-tier relationship labels (with kind + usage count) — discovery
    before coining a new label or consolidating synonyms via apply_reflection."""
    counts: dict[tuple[str, str], int] = {}
    seen_edges: set[str] = set()
    for node in await storage.query_nodes():
        for e in list(await storage.get_edges_from(node.id)) + list(
            await storage.get_edges_to(node.id)
        ):
            if e.type == EdgeType.RELATED and e.id not in seen_edges:
                seen_edges.add(e.id)
                counts[(e.label or "", e.kind)] = counts.get((e.label or "", e.kind), 0) + 1

    relations = [
        {"label": label, "kind": kind, "count": c}
        for (label, kind), c in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    result = {"relations": relations}
    meta = ResponseMeta(nodes_returned=len(relations))
    return result, meta


# --- Link ---


async def link(
    src_id: str,
    dst_id: str,
    storage: StorageBackend,
    *,
    edge_type: str | None = None,
    relation: str | None = None,
    kind: str = "relationship",
    weight: float = 1.0,
    metadata: dict | None = None,
) -> tuple[dict, ResponseMeta]:
    """Create a direct edge between two existing nodes.

    Give either `edge_type` (a known engine EdgeType) or `relation` (a free
    user-defined label → a RELATED edge). For a user relation, `kind` is
    "relationship" (followed in retrieval) or "attribution" (not); a label already
    in use reuses its existing kind (set once per label).
    """
    if relation is not None:
        et = EdgeType.RELATED
        resolved_kind = await storage.get_relation_kind(relation) or kind
        label = relation
    elif edge_type is not None:
        try:
            et = EdgeType(edge_type)
        except ValueError:
            valid = [e.value for e in EdgeType]
            raise ValueError(f"Invalid edge_type '{edge_type}'. Valid types: {valid}")
        resolved_kind = "relationship"
        label = None
    else:
        raise ValueError("link requires either edge_type or relation")

    # Verify both nodes exist
    if await storage.get_node(src_id) is None:
        raise ValueError(f"Source node '{src_id}' not found")
    if await storage.get_node(dst_id) is None:
        raise ValueError(f"Destination node '{dst_id}' not found")

    edge = NodeEdge(
        src_id=src_id,
        dst_id=dst_id,
        type=et,
        label=label,
        kind=resolved_kind,
        weight=weight,
        metadata=metadata or {},
    )
    await storage.store_edge(edge)

    result = {"edge_id": edge.id, "kind": resolved_kind}
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
    #
    # `extraction_method` carries over for the same reason: correcting the
    # wording does not change where the material came from.
    carried_value = old_node.value.model_copy()
    carried_method = old_node.extraction_method
    if isinstance(old_node, Topic):
        new_node: EpistemicNode = Topic(
            content=new_content, source_id=old_node.source_id,
            value=carried_value, extraction_method=carried_method,
        )
    elif isinstance(old_node, Fact):
        new_node = Fact(
            content=new_content, source_id=old_node.source_id,
            value=carried_value, extraction_method=carried_method,
        )
    elif isinstance(old_node, Inference):
        new_node = Inference(
            content=new_content, source_id=old_node.source_id,
            value=carried_value, extraction_method=carried_method,
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


async def reinforce(
    node_id: str,
    reason: str,
    storage: StorageBackend,
    *,
    related_id: str | None = None,
    importance_step: float = DEFAULT_IMPORTANCE_STEP,
) -> tuple[dict, ResponseMeta]:
    """Raise a node's `importance` and record why.

    The explicit upward path: an agent that learns something making an existing
    node matter more has nowhere else to put that. `relevance` is owned by the
    decay clock, so a judgment written there quietly decays back out.

    Not a raw setter, deliberately. Every bump appends
    `{at, reason, related_id}` to `metadata["reinforcements"]`, so a human
    reviewing a trivial-looking node rated highly can see the justification
    rather than an unattributable number. The step is asymptotic
    (`importance += step × (1 − importance)`), so repeated reinforcement
    approaches 1.0 instead of pinning there on the second call.

    `related_id` is validated rather than trusted: a dangling reference in a
    provenance trail is worse than no reference, because it reads as evidence.
    """
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    if related_id is not None and await storage.get_node(related_id) is None:
        raise ValueError(f"Related node '{related_id}' not found")

    at = datetime.now(timezone.utc)
    node.value = node.value.model_copy(update={
        "importance": node.value.importance
        + importance_step * (1.0 - node.value.importance),
    })
    node.metadata = {
        **node.metadata,
        "reinforcements": [
            *node.metadata.get("reinforcements", []),
            {"at": at.isoformat(), "reason": reason, "related_id": related_id},
        ],
    }
    await storage.store_node(node)

    result = {
        "node_id": node.id,
        "importance": node.value.importance,
        "reinforcements": len(node.metadata["reinforcements"]),
    }
    return result, ResponseMeta(nodes_returned=1)


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


# The phases `reflect` reports to the visualization strip, in execution order.
# Named here so the topology and the calls below cannot drift apart.
REFLECT_PHASES = (
    "decay",
    "topic_consolidation",
    "split_detection",
    "enrichment_scan",
    "contradiction_detection",
    "pending_review",
    "archival_nomination",
    "relation_consolidation",
)


async def reflect(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.85,
    decay_rate: float = 0.05,
    relation_similarity_threshold: float = 0.9,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Analyse the memory graph and return candidates for the agent to act on.

    Runs embedding-based analysis and value decay (applied immediately).
    Returns split candidates, similar topic pairs, enrichment candidates,
    contradiction pairs, and similar relationship-label pairs for the agent to
    review and act on via memory.apply_reflection.
    """
    from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
    from epimemer.pipelines.reflection.archival import nominate_archival_candidates
    from epimemer.pipelines.reflection.relation_consolidation import find_similar_relation_pairs
    from epimemer.pipelines.reflection.topic_consolidation import find_similar_topic_pairs
    from epimemer.pipelines.reflection.topic_enrichment import gather_associated_material, _should_enrich
    from epimemer.pipelines.reflection.topic_splitting import should_split
    from epimemer.pipelines.reflection.review import (
        frame_resolver,
        gather_pending_review,
        same_frame,
    )
    from epimemer.pipelines.reflection import value_decay
    from epimemer.visualization.phase_events import phase_pipeline

    model_id = embedding_provider.model_id

    # 1. Decay (applied immediately — no agent input needed)
    async def _decay():
        return await value_decay.apply_decay(storage, decay_rate=decay_rate)

    # 2. Find similar topic pairs for consolidation
    async def _consolidation():
        pairs = await find_similar_topic_pairs(
            storage, embedding_provider,
            similarity_threshold=similarity_threshold,
            model_id=model_id,
        )
        return [
            {
                "topic_a": {"id": a.id, "content": a.content},
                "topic_b": {"id": b.id, "content": b.content},
                "similarity": round(score, 4),
            }
            for a, b, score in pairs
        ]

    # 3. Find split candidates (topics with high internal variance)
    async def _splits():
        candidates = []
        for topic in await _active_topics():
            material = await _material_for(topic)
            if len(material) < 4:
                continue
            material_vectors = await embedding_provider.embed(material)
            if should_split(material_vectors):
                candidates.append({
                    "topic_id": topic.id,
                    "topic_content": topic.content,
                    "material": material,
                })
        return candidates

    # 4. Find enrichment candidates (thin descriptions with rich material)
    async def _enrichment():
        candidates = []
        for topic in await _active_topics():
            material = await _material_for(topic)
            if _should_enrich(topic, material, material_ratio=3.0):
                candidates.append({
                    "topic_id": topic.id,
                    "current_content": topic.content,
                    "associated_material": material,
                })
        return candidates

    # Split detection and the enrichment scan walk the same topic set. Fetched
    # once and reused: a second full scan would add to exactly the N+1 cost
    # (ISSUES.md #14) that makes reflect the slowest operation here. Lazy rather
    # than hoisted so the fetch stays attributed to the phase that needs it
    # first.
    topic_cache: list[Topic] = []

    async def _active_topics() -> list[Topic]:
        if not topic_cache:
            all_topics = await storage.query_nodes(node_type=NodeType.TOPIC)
            topic_cache.extend(t for t in all_topics if isinstance(t, Topic))
        return topic_cache

    # Both phases want the same material, and gathering it is two full edge
    # scans per topic. Read once, scoped to this call so nothing goes stale.
    material_cache: dict[str, list[str]] = {}

    async def _material_for(topic: Topic) -> list[str]:
        if topic.id not in material_cache:
            material_cache[topic.id] = await gather_associated_material(topic, storage)
        return material_cache[topic.id]

    # 5. Detect contradictions (safety net for anything ingest-time check missed).
    #    Similarity nominates; keep only same-frame pairs — a high-similarity pair
    #    across disjoint metacontext frames is coexistence, not a contradiction.
    async def _contradictions():
        raw = await detect_contradictions(
            storage, embedding_provider,
            similarity_threshold=0.80,
            model_id=model_id,
        )
        # One resolver for the whole pass: candidate pairs are quadratic in
        # facts while the facts themselves are not, and an uncached frame lookup
        # is a full edge scan.
        resolve_frames = frame_resolver(storage)
        found = []
        for a, b, score in raw:
            if not await same_frame(a.id, b.id, storage, resolve=resolve_frames):
                continue
            found.append({
                "fact_a": {"id": a.id, "content": a.content},
                "fact_b": {"id": b.id, "content": b.content},
                "similarity": round(score, 4),
            })
        return found

    # 6. Surface the pending-review worklist: active nodes already carrying review
    #    state (a candidate to supersede, stale evidence, or an unresolved
    #    contest), with the related ids to act on via apply_reflection /
    #    supersede_by / record_variant.
    async def _pending_review():
        return [
            {
                "node": {"id": n.id, "content": n.content, "node_type": _node_type_key(n)},
                "review": labels,
            }
            for n, labels in await gather_pending_review(storage)
        ]

    # 7. Nominate archival candidates — the hygiene arm of the same loop, and a
    #    worklist in the same shape as pending_review. Mechanical: no LLM, no
    #    embeddings. The agent judges, the human approves, and
    #    apply_reflection(archivals=[...]) applies.
    async def _archival():
        return [
            c.model_dump(mode="json")
            for c in await nominate_archival_candidates(storage)
        ]

    # 8. Find likely-synonymous user relationship labels (open vocabulary captured
    #    fast, organized slow). Applied via apply_reflection relation_merges.
    async def _relations():
        return await find_similar_relation_pairs(
            storage, embedding_provider,
            similarity_threshold=relation_similarity_threshold,
        )

    # Reflect is the longest operation in the system and the one users most want
    # to watch, but it is plain functions rather than a Petri net — so it
    # declares a synthetic linear topology and fires it by hand. Without a bus
    # `phase` is a bare await, so watching cannot change what is computed.
    async with phase_pipeline(event_bus, "reflect", REFLECT_PHASES) as phase:
        nodes_decayed = await phase("decay", _decay, tokens=int)
        similar_pairs = await phase("topic_consolidation", _consolidation, tokens=len)
        split_candidates = await phase("split_detection", _splits, tokens=len)
        enrichment_candidates = await phase("enrichment_scan", _enrichment, tokens=len)
        contradictions = await phase(
            "contradiction_detection", _contradictions, tokens=len
        )
        pending_review = await phase("pending_review", _pending_review, tokens=len)
        archival_candidates = await phase(
            "archival_nomination", _archival, tokens=len
        )
        similar_relations = await phase(
            "relation_consolidation", _relations, tokens=len
        )

    result = {
        "nodes_decayed": nodes_decayed,
        "similar_pairs": similar_pairs,
        "split_candidates": split_candidates,
        "enrichment_candidates": enrichment_candidates,
        "contradictions": contradictions,
        "pending_review": pending_review,
        "archival_candidates": archival_candidates,
        "similar_relations": similar_relations,
    }
    meta = ResponseMeta(
        nodes_returned=(
            len(similar_pairs) + len(split_candidates)
            + len(enrichment_candidates) + len(contradictions)
            + len(pending_review) + len(archival_candidates)
            + len(similar_relations)
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
    archivals: list[str] | None = None,
    relation_merges: list[dict] | None = None,
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
        (Sources, tags, and entities are also Topics, so they consolidate here.)
    supersessions: [{old_id: str, by_id: str}] — resolve a flagged/contested node
        (from reflect's pending_review) by superseding ``old_id`` with an existing
        node ``by_id``. Atomic: marks old superseded (lineage old → by), flags
        inferences that depended on old as evidence_stale, and clears any
        supersession candidacy on it. The winner is unchanged; no new node.
    archivals: [node_id] — archive the approved nodes from reflect's
        archival_candidates. Exports each node with its edges (returned as
        ``archive_data`` — keep it; that copy is the archive) and atomically
        flips them to ARCHIVED, which removes them from every active-status
        query. Nothing is deleted, and ``restore`` reverses it. Unknown or
        already-retired ids are skipped, as supersessions are.
    relation_merges: [{labels: [str], into: str}] — consolidate synonymous user
        relationship labels (from reflect's similar_relations). Every user-tier
        edge with a listed label is relabelled to ``into``, in place (edges are
        not versioned).
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
            source_id=children[0].source_id,
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
            Topic(
                content=sc, source_id=parent.source_id, extraction_method="agent:split",
                metadata={"split_from": topic_id},
            )
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
            # Max, as above: collapsing topics must not discard a judgment.
            importance=max(s.value.importance for s in sources),
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

    # 6. Archive the approved trivial nodes: export first, then one atomic flip.
    #    Ordering matters — the export is the archive, so it must be taken
    #    before anything about the nodes changes.
    to_archive: list[EpistemicNode] = []
    for node_id in (archivals or []):
        node = await storage.get_node(node_id)
        if node is None or node.status is not NodeStatus.ACTIVE:
            continue
        to_archive.append(node)

    archive_data: dict = {"nodes": [], "edges": []}
    if to_archive:
        from epimemer.pipelines.reflection.archival import archive_nodes

        archive_data = await archive_nodes(to_archive, storage)
        await storage.set_node_status_tx(
            to_archive,
            status=NodeStatus.ARCHIVED,
            retired_at=datetime.now(timezone.utc),
        )

    # 7. Consolidate synonymous user relationship labels: relabel edges in place
    #    (edges are not versioned).
    relations_consolidated = 0
    edges_relabeled = 0
    for rm_spec in (relation_merges or []):
        into = rm_spec["into"]
        applied = False
        for label in rm_spec["labels"]:
            if label == into:
                continue
            n = await storage.relabel_edges(label, into)
            edges_relabeled += n
            if n:
                applied = True
        if applied:
            relations_consolidated += 1

    result = {
        "parents_created": parents_created,
        "topics_split": topics_split,
        "topics_enriched": topics_enriched,
        "topics_merged": topics_merged,
        "merges_rejected": merges_rejected,
        "supersessions_applied": supersessions_applied,
        "nodes_archived": len(to_archive),
        "archive_data": archive_data,
        "relations_consolidated": relations_consolidated,
        "edges_relabeled": edges_relabeled,
    }
    meta = ResponseMeta(
        nodes_returned=(
            parents_created + topics_split + topics_enriched
            + topics_merged + supersessions_applied + len(to_archive)
            + relations_consolidated
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
    """Restore nodes and edges from archive data.

    Two shapes of archive reach this, and they need different writes.

    A *cold-storage reimport* brings back records the graph no longer holds:
    those are reconstructed first (so a malformed record fails before anything
    is written) and persisted in a single ``write_batch_tx`` — all of it lands
    or none of it does.

    An *un-archival* is the reversal of the hygiene sweep, and there the rows
    are still present: `archive` never deletes, it flips status. Re-inserting
    them would write nothing, so anything already stored as ARCHIVED is flipped
    back to ACTIVE instead. Records present under any other status are left
    alone — restoring an archive must not resurrect a node that was superseded
    for being wrong.
    """
    nodes = [_reconstruct_node(nd) for nd in archive_data.get("nodes", [])]
    edges = [NodeEdge(**ed) for ed in archive_data.get("edges", [])]

    missing: list[EpistemicNode] = []
    archived: list[EpistemicNode] = []
    for node in nodes:
        stored = await storage.get_node(node.id)
        if stored is None:
            missing.append(node)
        elif stored.status is NodeStatus.ARCHIVED:
            archived.append(stored)

    # Only edges reaching a node that was itself missing can be missing: an
    # edge between two nodes still in the graph was never removed.
    missing_ids = {node.id for node in missing}
    missing_edges = [
        edge for edge in edges
        if edge.src_id in missing_ids or edge.dst_id in missing_ids
    ]

    await storage.write_batch_tx(nodes=missing, edges=missing_edges)
    if archived:
        await storage.set_node_status_tx(
            archived, status=NodeStatus.ACTIVE, retired_at=None
        )

    result = {
        "nodes_restored": len(missing),
        "nodes_reactivated": len(archived),
        "edges_restored": len(missing_edges),
    }
    meta = ResponseMeta(nodes_returned=len(missing) + len(archived))
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


def _reference_time_iso(timeline: Timeline) -> str | None:
    """A timeline's reference time as ISO, or None when it follows the clock."""
    return (
        None if timeline.reference_time is None
        else timeline.reference_time.isoformat()
    )


async def create_timeline(
    name: str,
    storage: StorageBackend,
    *,
    description: str = "",
    reference_time: datetime | None = None,
) -> tuple[dict, ResponseMeta]:
    """Create a new timeline.

    `reference_time` is the timeline's own "now" — set it for a fictional or
    historical timeline whose present is not the wall clock. Leaving it unset
    means the timeline tracks real time, which is not the same as pinning it to
    the instant of creation.
    """
    tl = Timeline(name=name, description=description, reference_time=reference_time)
    await storage.store_timeline(tl)
    result = {
        "timeline_id": tl.id,
        "name": tl.name,
        "reference_time": _reference_time_iso(tl),
    }
    meta = ResponseMeta(nodes_returned=1)
    return result, meta


async def set_reference_time(
    timeline_id: str,
    storage: StorageBackend,
    *,
    reference_time: datetime | None = None,
) -> tuple[dict, ResponseMeta]:
    """Set (or clear) a timeline's reference time.

    Separate from creation because a fiction's present is often learned later,
    and read wrong first — the opening chapter dates the story only once you
    have read it. Passing nothing clears the setting, returning the timeline to
    the wall clock.
    """
    tl = await storage.get_timeline(timeline_id)
    if tl is None:
        raise ValueError(f"Timeline '{timeline_id}' not found")

    # Copy-with-update rather than mutate-and-store: `store_timeline` is an
    # upsert of the whole record, so the timepoints have to travel with it.
    updated = tl.model_copy(update={"reference_time": reference_time})
    await storage.store_timeline(updated)

    result = {
        "timeline_id": updated.id,
        "name": updated.name,
        "reference_time": _reference_time_iso(updated),
    }
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
        # Reported on every query so a caller reading timepoints can tell which
        # of them are past and which are future without a second call.
        "reference_time": _reference_time_iso(tl),
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


async def effective_reflect_threshold(
    storage: StorageBackend, default: int
) -> int:
    """The threshold in force for the active graph: its override, else `default`.

    For callers that need only the number. `graph_stats` reads the override
    itself — it reports whether one is set — and resolves it with
    `resolve_reflect_threshold` rather than fetching twice.
    """
    return resolve_reflect_threshold(
        await storage.get_reflect_threshold_override(), default
    )


async def configure_reflection(
    storage: StorageBackend,
    *,
    threshold: int | None,
    default_threshold: int,
) -> tuple[dict, ResponseMeta]:
    """Set the active graph's reflect threshold, or clear it back to the default.

    `threshold=None` clears the override — the graph then follows whatever the
    process default is at the time, rather than freezing today's value.

    Deliberately does not touch the counter: raising the threshold means "not
    yet", and zeroing the count would discard the accumulated signal instead of
    deferring it.
    """
    if threshold is not None and threshold < 1:
        raise ValueError(f"threshold must be at least 1, got {threshold}")

    await storage.set_reflect_threshold_override(threshold)

    count = await storage.get_reflect_counter()
    effective = await effective_reflect_threshold(storage, default_threshold)
    result = {
        "graph": storage.current_database,
        "reflect_threshold": effective,
        "overridden": threshold is not None,
        "default_threshold": default_threshold,
        "stores_since_reflect": count,
        "reflect_suggested": count >= effective,
    }
    return result, ResponseMeta()


async def graph_stats(
    storage: StorageBackend, *, default_reflect_threshold: int
) -> tuple[dict, ResponseMeta]:
    """Summarize the active graph: node counts by type, edge counts by type, totals.

    Aggregate-only — does not materialize node or edge bodies.

    Also reports reflection pressure: the graph's store counter, the threshold in
    force, whether that threshold is a per-graph override, and whether a reflect
    is due. The counter and any override are stored per graph; the default is
    process config, so it is passed in. These keys are always present — an absent
    key reads the same as `false` to a caller, and this is a readout meant to be
    checked.
    """
    node_counts = await storage.count_nodes_by_type()
    edge_counts = await storage.count_edges_by_type()
    metacontexts = await storage.query_metacontexts()
    timelines = await storage.query_timelines()
    stores_since_reflect = await storage.get_reflect_counter()
    threshold_override = await storage.get_reflect_threshold_override()
    reflect_threshold = resolve_reflect_threshold(
        threshold_override, default_reflect_threshold
    )

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
        "stores_since_reflect": stores_since_reflect,
        "reflect_threshold": reflect_threshold,
        "reflect_threshold_overridden": threshold_override is not None,
        # Inclusive, matching store_decomposition — the two readouts must not
        # disagree about whether a reflect is due.
        "reflect_suggested": stores_since_reflect >= reflect_threshold,
    }
    meta = ResponseMeta(nodes_returned=total_nodes, source_types=nodes_by_type)
    return result, meta


def _reject_invalid_graph_name(name: str) -> tuple[dict, ResponseMeta] | None:
    """Return an error response for an illegal graph name, else None.

    The storage backends raise on these too (defence in depth); this layer turns
    it into a result the calling agent can read and act on.
    """
    try:
        validate_graph_name(name)
    except ValueError as exc:
        return {"status": "invalid_name", "message": str(exc)}, ResponseMeta()
    return None


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
    invalid = _reject_invalid_graph_name(name)
    if invalid is not None:
        return invalid

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
    invalid = _reject_invalid_graph_name(name)
    if invalid is not None:
        return invalid

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
