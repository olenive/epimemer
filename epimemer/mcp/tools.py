"""Core tool implementations for the Epimemer MCP server.

Each function is a pure async function with explicit dependencies —
no global state, easily testable. The MCP server layer in server.py
calls these and wraps the results.
"""

from datetime import datetime, timezone
from typing import Iterable, Iterator, Literal, Mapping, Sequence

from pydantic import BaseModel, Field

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
    NOMINATED_STATUSES,
    RESTORABLE_STATUSES,
    superseded_status_for,
    NodeType,
    RawDocument,
    Segment,
    Timeline,
    Topic,
    ValueSignal,
    merged_value_signal,
)
from epimemer.core.temporal import ValidityInterval
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.mcp.config import (
    DEFAULT_IMPORTANCE_STEP,
    DEFAULT_RECORD_RETRIEVAL,
    ServerConfig,
)
from epimemer.mcp.retrieval_records import RetrievedNode
from epimemer.mcp.types import ResponseMeta
from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment
from epimemer.pipelines.query.types import SeedProvenance
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


# --- Declaring what a response carries ---
#
# Every tool that puts a node id where the agent can read it says so on its
# `ResponseMeta`. The choke point in `server.py` writes the record; it does not
# guess the ids, because walking an arbitrary result dict for id-shaped keys
# would guess differently per tool and break silently when a shape changed
# (`RETRIEVAL_PROVENANCE.md` §2.1).
#
# The rule is semantic rather than a list of tools: **`retrieved` is the set of
# node ids present in the response** — what the agent saw. The enumeration in
# §2 was wrong twice for exactly the reason a list is the wrong shape.


def _declare(
    node_ids: Iterable[str],
    *,
    provenance: SeedProvenance | Mapping[str, SeedProvenance] = SeedProvenance.DIRECT,
    scores: Mapping[str, float] | None = None,
) -> list[RetrievedNode]:
    """The declaration for a response carrying `node_ids`.

    Deduplicated, first appearance winning, so a node reached twice is declared
    once and in the order the response lists it. `DIRECT` is the default because
    most tools return nodes without ranking them at all; a ranked tool passes
    its own map.
    """
    declared: dict[str, RetrievedNode] = {}
    for node_id in node_ids:
        if node_id in declared:
            continue
        declared[node_id] = RetrievedNode(
            node_id=node_id,
            provenance=(
                provenance.get(node_id, SeedProvenance.DIRECT)
                if isinstance(provenance, Mapping)
                else provenance
            ),
            score=None if scores is None else scores.get(node_id),
        )
    return list(declared.values())


_NESTED_ID_KEYS = ("id", "node_id", "topic_id")


def _ids_within(value: object) -> Iterator[str]:
    """Every node id nested anywhere in a result structure this tool just built.

    Used by `reflect` alone, whose seven nominee lists have seven shapes.
    Reading them off a hand-written list of key paths is how the eighth shape
    would go undeclared, and §2.1's objection does not apply here: it is about
    the *choke point* guessing across tools it knows nothing about, where this
    is a tool reading the structure it wrote three lines earlier.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _NESTED_ID_KEYS and isinstance(item, str):
                yield item
            else:
                yield from _ids_within(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _ids_within(item)


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
    published_at: dict | None = None,
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
    to the document by a `published_by` (attribution) edge. `published_at` is when
    the document was published, which bounds what it could have known; it is left
    absent rather than falling back to the ingest time (#53 T1 §7).
    """
    from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net
    from epimemer.pipelines.segmentation.semantic_similarity import semantic_similarity_segmentation_net

    strategy = segmentation_strategy or config.segmentation_strategy

    doc = RawDocument(
        content=content, source=source, source_type=source_type,
        published_at=published_at, metadata=metadata or {},
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


class DecompositionEntry(BaseModel):
    """One extracted node as the agent supplied it.

    Both value fields are *priors*, not verdicts, and they differ in what
    omitting one says. `importance` has a real default: triviality is only
    visible once the neighbourhood exists, so the judgment happens at reflect
    time and a node that arrives unrated is simply waiting for it.
    `confidence` cannot be judged later by anything — the material is in front
    of the agent now and nothing downstream will read it again — so an omitted
    value means the question was never put, and stays absent rather than
    landing on the default number (#46).

    No bounds here: `ValueSignal` already holds them, and restating a range in
    two places is how the two come to disagree.
    """
    content: str
    tags: list[str] = Field(default_factory=list)
    importance: float | None = None
    confidence: float | None = None
    # One line, optional, and asked for by guidance rather than enforced. A
    # non-default prior with no reason recorded is the unattributable judgment
    # `judge_importance` refuses outright; here the same argument buys a
    # request, not a refusal, because failing an ingest over it costs more
    # than the missing line.
    confidence_basis: str | None = None
    # When this document says the claim was true (#53 T1 §9). Ingest is the only
    # place that can supply it: tense and the dates written in the text are
    # visible here and nowhere later, and reflect has facts and a graph rather
    # than a document. It lands on the node's `sourced_from` edge, so it is
    # always attributable to the document it came from.
    validity: list[ValidityInterval] = Field(default_factory=list)


def _decomposition_entry(entry) -> DecompositionEntry:
    """Unpack a decomposition entry: a bare content string, or a dict of the
    fields above. A bare string is the common case and carries no priors."""
    if isinstance(entry, dict):
        return DecompositionEntry.model_validate(entry)
    return DecompositionEntry(content=entry)


def _entry_value_signal(entry: DecompositionEntry) -> ValueSignal:
    """The priors the agent supplied, and nothing it did not.

    Naming a field at all is what distinguishes "rated 0.5" from "unrated", so
    an omitted one is left out of the call rather than passed as `None` — the
    model's own default is then the single place each field's absence is
    defined.
    """
    supplied = {
        name: value
        for name, value in (
            ("importance", entry.importance),
            ("confidence", entry.confidence),
        )
        if value is not None
    }
    return ValueSignal(**supplied)


EXTRACTED_TIMELINE_NAME = "Extracted"


async def _extraction_timeline(
    storage: StorageBackend, timeline_id: str | None
) -> Timeline:
    """The timeline extraction should propose onto.

    One shared timeline per graph rather than one per document. The panel shows
    a single timeline at a time (`dev-docs/TIMELINE_VISUALISATION.md` §12.2), so
    a timeline per document turns every ingest into another near-empty entry in
    the selector and buries the marks. Provenance is not lost by sharing:
    every node carries a `sourced_from` edge to its document.

    A named timeline must already exist. Creating one silently would put the
    document on a timeline the caller cannot find, under a name they never
    chose — `create_timeline` is how a name comes into being.
    """
    if timeline_id is not None:
        timeline = await storage.get_timeline(timeline_id)
        if timeline is None:
            raise ValueError(f"Timeline '{timeline_id}' not found")
        return timeline

    for timeline in await storage.query_timelines():
        if timeline.name == EXTRACTED_TIMELINE_NAME:
            return timeline
    return Timeline(
        name=EXTRACTED_TIMELINE_NAME,
        description="Timepoints proposed from ingested text.",
    )


async def store_decomposition(
    document_id: str,
    segments: list[dict],
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    metacontext_id: str | None = None,
    tags: list[str] | None = None,
    timeline_id: str | None = None,
    propose_timepoints: bool = True,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Store agent-provided decomposition: topics, facts, inferences per segment.

    Each entry in segments should have:
        segment_id: str
        topics/facts/inferences: each a content string, or a dict of the
            `DecompositionEntry` fields — per-node tags plus the two value
            priors. `importance` defaults to 0.5; `confidence` is left *unrated*
            when omitted rather than defaulting, and an optional
            `confidence_basis` records why a supplied one was chosen. The ladder
            an agent calibrates against lives in `server.py`'s tool docstring,
            which is what an agent actually reads before ingesting.

    Every node gets a `sourced_from` edge to the originating document. `tags`
    (document-level) and per-node tags are resolved-or-created (by exact name) as
    Topics linked by `tagged_with` edges, so a repeated tag reuses one Topic.
    Everything is persisted in one atomic write.

    Temporal expressions in node content become timepoints on a timeline
    (`timeline_id`, or the shared extracted one), linked by `TIMELINK`. Only
    what the text states is resolved: "during the Renaissance" stays undated
    rather than being guessed into a date. Pass `propose_timepoints=False` to
    skip it entirely.
    """
    from epimemer.pipelines.graph_construction.edge_creation import DecomposedSegment, edge_creation_net
    # Imported as a module: the `propose_timepoints` flag above would otherwise
    # shadow the function of the same name.
    from epimemer.pipelines.timeline import functions as timeline_functions

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
    # Tag Topics are excluded: a tag is a name, not a statement, and a tag that
    # happens to read as a date would put a mark on the timeline for every node
    # carrying it.
    datable: list[tuple[str, str]] = []

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
        validity_by_node: dict[str, list[ValidityInterval]] = {}
        for cls, entries, bucket in (
            (Topic, seg_data.get("topics", []), topics),
            (Fact, seg_data.get("facts", []), facts),
            (Inference, seg_data.get("inferences", []), inferences),
        ):
            for entry in entries:
                parsed = _decomposition_entry(entry)
                node = cls(
                    content=parsed.content, source_id=segment_id,
                    value=_entry_value_signal(parsed), extraction_method="agent",
                    # Beside the `reinforcements` trail rather than on the
                    # signal: the basis is prose about one judgment, and
                    # `ValueSignal` is the numbers every ranker reads.
                    metadata=(
                        {"confidence_basis": parsed.confidence_basis}
                        if parsed.confidence_basis else {}
                    ),
                )
                bucket.append(node)
                if parsed.validity:
                    validity_by_node[node.id] = parsed.validity
                names = doc_tag_names + parsed.tags
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
        datable.extend((node.id, node.content) for node in seg_nodes)

        if seg_nodes:
            vectors = await embedding_provider.embed([n.content for n in seg_nodes])
            for node, vector in zip(seg_nodes, vectors):
                batch_embeddings.append(EmbeddingRecord(
                    item_id=node.id, model_id=embedding_provider.model_id, vector=vector,
                ))

        # Provenance: every node is sourced_from the originating document, and
        # the periods this document asserts the claim held ride on that edge —
        # the only place they are attributable to the source that made them.
        for node in seg_nodes:
            batch_edges.append(NodeEdge(
                src_id=node.id, dst_id=document_id, type=EdgeType.SOURCED_FROM,
                validity=validity_by_node.get(node.id, []),
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

    # Timepoints ride in the same write: a TIMELINK naming a timeline that was
    # never stored resolves to an empty row rather than an error, so a partial
    # write would fail silently.
    batch_timelines: list[Timeline] = []
    timepoints_proposed = 0
    if propose_timepoints:
        timeline = await _extraction_timeline(storage, timeline_id)
        timeline, timelinks, timepoints_proposed = timeline_functions.propose_timepoints(
            datable, timeline
        )
        batch_edges.extend(timelinks)
        # An unchanged timeline needs no write; a timeline nobody added a point
        # to was never stored in the first place.
        if timepoints_proposed:
            batch_timelines.append(timeline)

    # One atomic write for the entire document.
    await storage.write_batch_tx(
        nodes=batch_nodes,
        edges=batch_edges,
        embeddings=batch_embeddings,
        timelines=batch_timelines,
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
        "timepoints_proposed": timepoints_proposed,
        "historical_twins": await _historical_twins(batch_nodes, storage),
    }
    meta = ResponseMeta(
        nodes_returned=total_topics + total_facts + total_inferences,
        source_types={k: v for k, v in nodes_created.items() if v > 0},
    )
    return result, meta


async def _historical_twins(nodes: Sequence[EpistemicNode], storage) -> list[dict]:
    """Facts just stored that are word-for-word a claim the graph retired.

    The cheap floor under recurrence detection (#53 T2). `check_conflicts` is
    the load-bearing detector — it nominates by similarity, so it sees the
    recurrence two documents phrase differently, which is nearly all of them —
    but it is opt-in, and an agent that never calls it gets no recurrence
    detection at all. An exact-content match is the one case cheap enough to
    check unasked.

    **It reports and never acts.** Reactivation stays explicit: flipping a node
    live behind the caller's back on a string match is too brittle to do
    silently, and the agent has the new document in front of it and can tell a
    recurrence from a coincidence.

    Affordable only because #48 was fixed in the same visit: this is one indexed
    lookup per fact, 0.53 ms at 3,000 nodes against a real server, where the
    unhinted query it replaced was a table scan at 4.0 ms and climbing.
    """
    twins: list[dict] = []
    for node in nodes:
        if not isinstance(node, Fact):
            continue
        twin = await storage.get_node_by_content(
            node.content, node_type=NodeType.FACT, status=NodeStatus.HISTORICAL,
        )
        if twin is not None:
            twins.append({
                "fact_id": node.id,
                "content": node.content,
                "historical_id": twin.id,
            })
    return twins



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

    One query for the whole set. This was previously an `asyncio.gather` over a
    round-trip per node, which bought concurrency at the cost of issuing
    overlapping reads on the shared SurrealDB connection — the hazard ISSUES.md
    #16 describes. Batching is faster *and* sequential, so the trade goes away
    rather than being taken.
    """
    from epimemer.pipelines.reflection.review import frames_for

    frames_by_node = await frames_for([node.id for node in nodes], storage)
    return [
        node
        for node in nodes
        if metacontext_id in frames_by_node[node.id]
        or BASE_METACONTEXT_ID in frames_by_node[node.id]
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

    Both edge lookups are one query for the whole topic set, and neighbour
    bodies are fetched once each across it, reusing nodes the result already
    carries — so a parent and its children coming back together costs no extra
    fetches.
    """
    topics = [n for n in nodes if isinstance(n, Topic)]
    if not topics:
        return {}

    topic_ids = [topic.id for topic in topics]
    parent_edges = await storage.get_edges_for(
        topic_ids, direction="from", edge_type=EdgeType.SUBTOPIC_OF
    )
    child_edges = await storage.get_edges_for(
        topic_ids, direction="to", edge_type=EdgeType.SUBTOPIC_OF
    )

    neighbours_by_topic: dict[str, tuple[list[str], list[str]]] = {}
    needed: set[str] = set()
    for topic_id in topic_ids:
        parent_ids = [e.dst_id for e in parent_edges[topic_id]]
        child_ids = [e.src_id for e in child_edges[topic_id]]
        neighbours_by_topic[topic_id] = (parent_ids, child_ids)
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
        # id + preview is still "the agent saw this node".
        retrieved=_declare([node.id, *(a.id for a in ancestors), *visited]),
    )
    return result, meta


def retrieved_signal(value: ValueSignal, at: datetime) -> ValueSignal:
    """The value signal a node carries after being retrieved.

    Stamps `retrieved_at` and nothing else. Every other field is carried
    through unchanged — retrieval records *use*, and must not quietly restate a
    judgment held elsewhere in the signal. `importance_judged_at` is part of
    that: being read is not being judged.

    This used to also raise a `relevance` float asymptotically. That field was
    removed (no reader, and confounded by reflect frequency), which leaves the
    timestamp as the whole of what retrieval records — and makes this the
    complete answer to "when was this last used?".
    """
    return value.model_copy(update={"retrieved_at": at})


async def _record_retrieval(
    nodes: Sequence[EpistemicNode], storage: StorageBackend, enabled: bool
) -> None:
    """Stamp `retrieved_at` on every node search returned.

    Without this the only thing known about a node is its age, which says
    nothing about whether it is load-bearing — exactly the distinction archival
    candidacy needs, and the reason `never_retrieved` can mean what it says.

    It deliberately does **not** feed ranking: results stay ordered by
    similarity. Wiring use back into ranking creates a `retrieved → ranked
    higher → retrieved` loop under which popular nodes crowd out better
    matches. See `dev-docs/REVIEW_EPISTEMIC.md` §12.4.

    Costs one write per returned node, which is why it can be switched off.
    """
    if not enabled:
        return
    at = datetime.now(timezone.utc)
    for node in nodes:
        node.value = retrieved_signal(node.value, at)
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
    terms: list[str] | None = None,
    record_retrieval: bool = DEFAULT_RECORD_RETRIEVAL,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Search the memory graph: embedding similarity and keyword matching, fused.

    **Pass identifiers, names and exact phrases you care about as `terms`.**
    A ticket id, an error code, a person's name, a filename — anything where the
    exact string matters. Embeddings shred those: `JIRA-4417` becomes word
    pieces mean-pooled with the rest of the sentence, so the query embeds to
    roughly "short alphanumeric string" and every *other* ticket id in the graph
    scores about as well. Keyword matching supplies the term rarity that
    similarity has no notion of, and a declared term's best hit is kept in the
    results even if rank fusion would otherwise have cut it.

    Terms are matched whole and ORed: `terms=["JIRA-4417", "certificate
    rotation"]` finds nodes matching either, and each term matches only
    documents containing all of its words. Omit `terms` and the keyword arm
    falls back to the query's own words — rare ones still fire, common ones
    contribute nothing, and there is no survival guarantee. Declaring is the
    reliable path.

    Each returned node carries `provenance` saying how it was reached:
    `lexical` (an exact term matched its content), `segment` (a term matched the
    source passage it was extracted from), `vector` (embedding similarity), or
    `expanded` (reached by an edge from one of the above). The response also
    carries `segments` — the passages that matched, whether or not anything was
    extracted from them, since *where did I read that?* is a different question
    from *what do I believe?*

    If metacontext_id is provided, results are frame-scoped to that metacontext
    plus untagged base-reality nodes (set cross_frame=True to ignore frames).
    Frame-scoping over-fetches so an in-frame node ranked below the vector top-k
    is still found (see `_retrieve_frame_scoped`). Metacontext labels and computed
    review labels (superseded_candidate / evidence_stale / contested) are always
    included on returned nodes. Returned Topics that sit in a split hierarchy also
    carry `parents` / `subtopics` as id + preview, so the caller can drill via
    `topic_tree` instead of being handed the whole subtree.

    Returned nodes have `retrieved_at` stamped (`record_retrieval=False`
    disables): being retrieved is what tells a used node from a merely old one.
    Ranking is unaffected — see `_record_retrieval`.
    """
    from epimemer.pipelines.query.types import QueryRequest, SeedProvenance
    from epimemer.pipelines.reflection.review import review_labels_for

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
        terms=terms,
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
    await _record_retrieval(nodes, storage, record_retrieval)

    # Build node dicts with metacontext labels, computed review labels, and —
    # for topics in a split hierarchy — their neighbours, so the caller can
    # drill rather than be handed the whole subtree.
    hierarchy = await _hierarchy_annotations(nodes, storage)
    labels_by_node = await _metacontext_labels_for([n.id for n in nodes], storage)
    review_by_node = await review_labels_for(nodes, storage)

    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        # How this node was reached. Frame-scoping can hand back a node the
        # final run did not rank, so the label falls back to `expanded` rather
        # than being omitted — every returned node says something about itself.
        node_dict["provenance"] = query_result.provenance.get(
            node.id, SeedProvenance.EXPANDED
        ).value
        if labels_by_node[node.id]:
            node_dict["metacontexts"] = labels_by_node[node.id]
        if node.id in review_by_node:
            node_dict["review"] = review_by_node[node.id]
        node_dict.update(hierarchy.get(node.id, {}))
        nodes_data.append(node_dict)

    result = {
        "nodes": nodes_data,
        "edges": edges_data,
        # Passages the keyword arm matched, in their own right. A segment is not
        # a graph node and must not be pretended into one.
        "segments": [hit.model_dump(mode="json") for hit in query_result.segments],
    }
    meta = ResponseMeta(
        nodes_searched=query_result.metadata.nodes_searched,
        nodes_returned=len(nodes),
        graph_hops=query_result.metadata.graph_hops,
        source_types=query_result.metadata.source_types,
        # The provenance the response already carries, declared for the
        # dashboard. Same fallback as the serialized dict above, so the two
        # cannot disagree about how a node was reached.
        retrieved=_declare(
            (node.id for node in nodes),
            provenance={
                node.id: query_result.provenance.get(node.id, SeedProvenance.EXPANDED)
                for node in nodes
            },
        ),
    )
    return result, meta


# --- Temporal queries ---


def events_in_window(
    node: EpistemicNode, start: datetime, end: datetime,
) -> list[NodeChangeEvent]:
    """Lifecycle events on a node that fall in the half-open window [start, end).

    Emits `created` when the node was born in the window, and one event per
    lifecycle episode boundary that falls inside it: the status the retirement
    gave the node — retiring as `historical` and retiring as `corrected` are
    different things to report (#53) — with the counterpart that caused it, and
    `restored` where the node came back.

    The episodes are read rather than `(status, superseded_at)` because that
    pair holds only the *latest* transition: a node that retired, returned and
    retired again has three events and one `superseded_at`.
    """
    events: list[NodeChangeEvent] = []
    if start <= node.created_at < end:
        events.append(NodeChangeEvent(kind="created", at=node.created_at))

    for episode in node.lifecycle:
        if start <= episode.retired_at < end:
            events.append(NodeChangeEvent(
                kind=episode.because.value,
                at=episode.retired_at,
                counterpart=episode.counterpart,
            ))
        if episode.restored_at is not None and start <= episode.restored_at < end:
            events.append(NodeChangeEvent(kind="restored", at=episode.restored_at))

    # A retirement no episode records: a graph written before episodes existed.
    # Reported without a counterpart rather than dropped — old graphs are not
    # repaired, but they are still readable.
    if (
        node.superseded_at is not None
        and node.status is not NodeStatus.ACTIVE
        and all(ep.retired_at != node.superseded_at for ep in node.lifecycle)
        and start <= node.superseded_at < end
    ):
        events.append(
            NodeChangeEvent(kind=node.status.value, at=node.superseded_at)
        )

    return sorted(events, key=lambda event: event.at)


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
    meta = ResponseMeta(
        nodes_returned=len(nodes),
        source_types=source_types,
        retrieved=_declare(n.id for n in nodes),
    )
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
    from epimemer.pipelines.reflection.review import review_labels_for

    nt_enums = [NodeType(t) for t in node_types] if node_types else [None]

    windows_data = []
    total = 0
    source_types: dict[str, int] = {}
    # Across every window, since the record is per response and a node that
    # changed twice was still shown once.
    changed_ids: list[str] = []
    for start, end in windows:
        seen: dict[str, EpistemicNode] = {}
        for nt in nt_enums:
            for node in await storage.query_changes(start=start, end=end, node_type=nt):
                seen[node.id] = node

        changed = list(seen.values())
        labels_by_node = await _metacontext_labels_for([n.id for n in changed], storage)
        review_by_node = await review_labels_for(changed, storage)

        changes = []
        for node in changed:
            node_dict = _node_to_dict(node)
            node_dict["events"] = [
                e.model_dump(mode="json") for e in events_in_window(node, start, end)
            ]
            if labels_by_node[node.id]:
                node_dict["metacontexts"] = labels_by_node[node.id]
            if node.id in review_by_node:
                node_dict["review"] = review_by_node[node.id]
            changes.append(node_dict)
            changed_ids.append(node.id)

            key = _node_type_key(node)
            source_types[key] = source_types.get(key, 0) + 1
            total += 1

        windows_data.append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "changes": changes,
        })

    result = {"windows": windows_data}
    meta = ResponseMeta(
        nodes_returned=total,
        source_types=source_types,
        retrieved=_declare(changed_ids),
    )
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
    meta = ResponseMeta(
        nodes_returned=len(nodes),
        source_types=source_types,
        retrieved=_declare(n.id for n in nodes),
    )
    return result, meta


async def list_sources(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Distinct source/origin nodes with how many nodes reference each — the
    documents nodes are `sourced_from`, plus entities linked by attribution edges
    (e.g. published_by). Discovery before find_nodes."""
    node_ids = [node.id for node in await storage.query_nodes()]
    sourced_from = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.SOURCED_FROM
    )
    attributed = await storage.get_edges_for(
        node_ids, direction="to", edge_type=EdgeType.RELATED
    )

    counts: dict[str, int] = {}
    for node_id in node_ids:
        for e in sourced_from[node_id]:
            counts[e.dst_id] = counts.get(e.dst_id, 0) + 1
        for e in attributed[node_id]:
            if e.kind == "attribution":
                counts[node_id] = counts.get(node_id, 0) + 1

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
    # Documents among these are not graph nodes and simply never match one in
    # the dashboard; the entity topics are, and are the reason this declares.
    meta = ResponseMeta(
        nodes_returned=len(sources),
        retrieved=_declare(source["id"] for source in sources),
    )
    return result, meta


async def list_relations(storage: StorageBackend) -> tuple[dict, ResponseMeta]:
    """Distinct user-tier relationship labels (with kind + usage count) — discovery
    before coining a new label or consolidating synonyms via apply_reflection."""
    from epimemer.pipelines.reflection.relation_consolidation import (
        related_edges_of_active_nodes,
    )

    counts: dict[tuple[str, str], int] = {}
    for e in await related_edges_of_active_nodes(storage):
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
    *,
    because: str,
) -> tuple[dict, ResponseMeta]:
    """Update a node by creating a new version (supersession).

    The replacement is embedded so it remains searchable.

    `because` says which of two opposite things happened — `"it_was_wrong"` or
    `"the_world_changed"` — and has no default on purpose (#53). A claim that
    stopped being true was never an error, and recording it as one is how a
    graph forgets its own history.

    It also decides which edges follow the replacement (#54). A correction
    hands over everything but history and review: the old node is an audit husk
    and the replacement is the same claim, corrected. A world-change hands over
    the frame and the tags only — the retired node keeps its own provenance,
    because it is still true of its period and its sources are what say so.

    And it decides which lineage edge records the step: `superseded_by` says
    *replaced* and is terminal, `temporally_followed_by` says only *came after*
    and survives the same claim becoming true again (#53).
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

    edge = await supersede_node(
        old_node, new_node, storage, embedding_provider,
        status=superseded_status_for(because),
    )

    result = {
        "old_node_id": old_node.id,
        "new_node_id": new_node.id,
        "edge_id": edge.id,
    }
    meta = ResponseMeta(
        nodes_returned=2, retrieved=_declare([new_node.id, old_node.id])
    )
    return result, meta


JudgmentDirection = Literal["up", "down"]


def judged_importance(
    importance: float, direction: JudgmentDirection, step: float
) -> float:
    """`importance` after one judgment in `direction`.

    ::

        up:    importance += step * (1 - importance)     # asymptotic to 1.0
        down:  importance -= step * importance           # asymptotic to 0.0

    Each closes the gap to *its own* bound by the same fraction, so the two are
    mirrors — and deliberately **not** inverses. Up-then-down does not return
    home (0.5 -> 0.625 -> 0.469), and repeated alternation settles into a
    two-cycle straddling 0.5: {3/7, 4/7} at the default step. Neither side
    wins, so a node two agents disagree about parks at the un-judged default,
    with the most recent judgment deciding which side of the nomination ceiling
    it currently sits on.

    An exactly invertible form was considered and rejected twice over.
    ``(i - step)/(1 - step)`` returns home but goes negative below the step size
    and needs a clamp — invertible in the mid-range where nothing needs it,
    lossy near the floor where the nomination ceiling sits. Log-odds is
    genuinely both invertible and asymptotic, but costs the settable knob
    (``EPIMEMER_IMPORTANCE_STEP`` means "close a quarter of the remaining gap";
    a log-odds constant means nothing to anyone) and needs input clamping
    anyway. Both buy invertibility, which nothing here consumes: a later
    downward judgment is a new assessment on new information, not an undo, and
    the provenance trail keeps both entries deliberately.

    Neither direction reaches its bound, so arithmetic can never judge a node
    into certainty or out of existence.
    """
    if direction == "up":
        return importance + step * (1.0 - importance)
    if direction == "down":
        return importance - step * importance
    raise ValueError(f"Unknown direction '{direction}' - expected 'up' or 'down'")


async def judge_importance(
    node_id: str,
    direction: JudgmentDirection,
    reason: str,
    storage: StorageBackend,
    *,
    related_id: str | None = None,
    importance_step: float = DEFAULT_IMPORTANCE_STEP,
) -> tuple[dict, ResponseMeta]:
    """Move a node's `importance` by one judgment, and record why.

    The explicit path, in both directions: an agent that learns something making
    an existing node matter more — or less — has nowhere else to put it.
    Retrieval writes a timestamp, not a verdict, so being read a lot cannot
    stand in for having been judged.

    Named for the act rather than the outcome, which is what lets one tool carry
    both directions. `direction` is not ceremony wrapped around the judgment; it
    *is* the judgment — "this matters more than the graph currently thinks", or
    less.

    Not a raw setter, deliberately, and for two reasons beyond auditability. An
    agent setting `0.7` has not seen any other node's value and is guessing at a
    scale it cannot see, while "more than the graph thinks" is a judgment it can
    make well. And a setter is last-writer-wins: three judgments that took a
    node to 0.85 would be erased by one agent typing 0.6 six months later on a
    single conversation's context. Steps compose. (The one moment a setter is
    safe already exists — `store_decomposition`'s ingest prior, applied at
    creation before there is anything to overwrite.)

    Every judgment appends `{at, reason, related_id, direction}` to
    `metadata["reinforcements"]` — one chronological trail, because a reviewer
    wants a bump and its later reversal in sequence with both reasons. The key
    keeps its original name: renaming it is a data migration for a cosmetic
    gain. **An entry carrying no `direction` predates this tool and means "up".**

    `related_id` is validated rather than trusted: a dangling reference in a
    provenance trail is worse than no reference, because it reads as evidence.
    """
    node = await storage.get_node(node_id)
    if node is None:
        raise ValueError(f"Node '{node_id}' not found")

    if related_id is not None and await storage.get_node(related_id) is None:
        raise ValueError(f"Related node '{related_id}' not found")

    # Computed before any write, so an unknown direction leaves the node as it
    # was rather than half-judged.
    importance = judged_importance(node.value.importance, direction, importance_step)

    at = datetime.now(timezone.utc)
    node.value = node.value.model_copy(update={
        "importance": importance,
        # The judgment clock, and only that one. `retrieved_at` belongs to
        # retrieval: an assessment is not traffic, and archival nomination
        # reads the two for different reasons.
        "importance_judged_at": at,
    })
    node.metadata = {
        **node.metadata,
        "reinforcements": [
            *node.metadata.get("reinforcements", []),
            {
                "at": at.isoformat(),
                "reason": reason,
                "related_id": related_id,
                "direction": direction,
            },
        ],
    }
    await storage.store_node(node)

    result = {
        "node_id": node.id,
        "importance": node.value.importance,
        "direction": direction,
        "judgments": len(node.metadata["reinforcements"]),
    }
    return result, ResponseMeta(nodes_returned=1, retrieved=_declare([node.id]))


async def supersede_by(
    old_id: str,
    existing_id: str,
    storage: StorageBackend,
    *,
    because: str,
) -> tuple[dict, ResponseMeta]:
    """Supersede a node by an already-existing node.

    Use this where the current truth already exists in the graph (rather than
    arriving as new content). `because` distinguishes the two reasons that can
    be true of — `"it_was_wrong"` (a correction) or `"the_world_changed"` (the
    old claim still holds of its period); see #53. The old
    node is marked accordingly and joined to `existing_id` by the lineage edge
    that matches — `superseded_by` for a correction, `temporally_followed_by`
    for a world-change; inferences that depended on it are flagged
    evidence_stale; the existing node keeps its own edges. Unlike `update`, no
    new node is created.
    """
    from epimemer.pipelines.graph_construction.versioning import supersede_by_existing

    if old_id == existing_id:
        raise ValueError("A node cannot supersede itself")
    old = await storage.get_node(old_id)
    if old is None:
        raise ValueError(f"Node '{old_id}' not found")
    if await storage.get_node(existing_id) is None:
        raise ValueError(f"Node '{existing_id}' not found")

    edge = await supersede_by_existing(
        old, existing_id, storage, status=superseded_status_for(because)
    )
    result = {"superseded_id": old_id, "by_id": existing_id, "edge_id": edge.id}
    meta = ResponseMeta(
        nodes_returned=2, retrieved=_declare([existing_id, old_id])
    )
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
    """Find facts similar to the given facts, for the agent to judge.

    The recall stage of the review loop (REVIEW_EPISTEMIC.md §5.1): for each fact,
    vector-searches above ``threshold`` (excluding the fact itself) and returns the
    candidates with their similarity score, status, metacontext labels, and a
    same_frame flag. Similarity only *nominates* — the agent then classifies each
    candidate (redundant / supersedes / recurs / contradicts / cross-frame /
    compatible) and records the verdict via supersede_by / restore /
    record_contradiction / record_variant. Opt-in and cheap: a single vector
    lookup per fact at a high bar.

    **Candidates include `historical` nodes, and that is what makes `recurs`
    reachable.** A claim retired because the world moved on can become true
    again — Labour out of government in 2010 and back in 2024 — and until this
    nomination included it, nobody was ever asked: ingest saw no twin and wrote
    a second node saying what the first one said. `corrected` nodes stay out,
    because a claim concluded *wrong* has no route back and nominating it would
    invite a verdict that cannot be recorded (#53 T2).

    Each candidate carries its `status` for the same reason. Once retired nodes
    can appear, an agent cannot tell an active twin from a historical one — and
    that distinction is the entire basis for choosing between `redundant` and
    `recurs`.
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
            statuses=NOMINATED_STATUSES,
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
                "status": cand.status.value,
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
    # The review loop's front door. Candidates are `vector` with the cosine as
    # the score — they genuinely are similarity results, so no new provenance
    # value is needed for them (§3, amended). The source facts are declared too:
    # the agent read their content here.
    meta = ResponseMeta(
        nodes_returned=candidate_count,
        retrieved=_declare(
            [
                *(c["fact"]["id"] for c in conflicts),
                *(cand["id"] for c in conflicts for cand in c["candidates"]),
            ],
            provenance={
                cand["id"]: SeedProvenance.VECTOR
                for c in conflicts
                for cand in c["candidates"]
            },
            scores={
                cand["id"]: cand["score"]
                for c in conflicts
                for cand in c["candidates"]
            },
        ),
    )
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
    "topic_consolidation",
    "split_detection",
    "enrichment_scan",
    "contradiction_detection",
    "recurrence_detection",
    "pending_review",
    "archival_nomination",
    "relation_consolidation",
)


async def reflect(
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    similarity_threshold: float = 0.85,
    relation_similarity_threshold: float = 0.9,
    event_bus: InProcessEventBus | None = None,
) -> tuple[dict, ResponseMeta]:
    """Analyse the memory graph and return candidates for the agent to act on.

    Reads only. Returns split candidates, similar topic pairs, enrichment
    candidates, contradiction pairs, archival nominations and similar
    relationship-label pairs for the agent to review and act on via
    memory.apply_reflection — nothing here changes the graph.
    """
    from epimemer.pipelines.reflection.contradiction_detection import detect_contradictions
    from epimemer.pipelines.reflection.archival import nominate_archival_candidates
    from epimemer.pipelines.reflection.relation_consolidation import find_similar_relation_pairs
    from epimemer.pipelines.reflection.topic_consolidation import find_similar_topic_pairs
    from epimemer.pipelines.reflection.topic_enrichment import gather_associated_material_for, _should_enrich
    from epimemer.pipelines.reflection.topic_splitting import should_split
    from epimemer.pipelines.reflection.review import (
        frame_resolver,
        frames_for,
        gather_pending_review,
        same_frame,
    )
    from epimemer.visualization.phase_events import phase_pipeline

    model_id = embedding_provider.model_id

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

    # Both phases want the same material. Gathered for every topic in one go the
    # first time either asks, and scoped to this call so nothing goes stale.
    material_cache: dict[str, list[str]] = {}

    async def _material_for(topic: Topic) -> list[str]:
        if not material_cache:
            material_cache.update(
                await gather_associated_material_for(await _active_topics(), storage)
            )
        return material_cache.get(topic.id, [])

    # 5. Detect contradictions (safety net for anything ingest-time check missed).
    #    Similarity nominates; keep only same-frame pairs — a high-similarity pair
    #    across disjoint metacontext frames is coexistence, not a contradiction.
    async def _same_frame_pairs(raw):
        """Drop cross-frame pairs — coexistence, not conflict — and shape them.

        One resolver for the whole pass, warmed in a single query: candidate
        pairs are quadratic in facts while the facts themselves are not, so the
        set to load is known from `raw` before any pair is checked.
        """
        candidate_ids = list({fact.id for pair in raw for fact in pair[:2]})
        resolve_frames = frame_resolver(
            storage,
            seed=await frames_for(candidate_ids, storage) if candidate_ids else None,
        )
        found = []
        for a, b, score in raw:
            if not await same_frame(a.id, b.id, storage, resolve=resolve_frames):
                continue
            found.append({
                "fact_a": {"id": a.id, "content": a.content, "status": a.status.value},
                "fact_b": {"id": b.id, "content": b.content, "status": b.status.value},
                "similarity": round(score, 4),
            })
        return found

    # Scored once over the nominated set and partitioned twice. This phase is
    # the one that crosses the tool timeout as a graph grows (#39), so widening
    # it to see historical facts must not also mean scoring the matrix twice —
    # the pairs are quadratic and the split is free.
    nominated_pairs: list = []

    async def _contradictions():
        nominated_pairs.extend(await detect_contradictions(
            storage, embedding_provider,
            similarity_threshold=0.80,
            model_id=model_id,
            statuses=NOMINATED_STATUSES,
        ))
        return await _same_frame_pairs([
            pair for pair in nominated_pairs
            if pair[0].status is NodeStatus.ACTIVE
            and pair[1].status is NodeStatus.ACTIVE
        ])

    # 5b. Recurrence, the safety net's other half: a live claim that says what a
    #     retired-because-the-world-moved-on one said (#53 T2). Reported apart
    #     from the contradictions, because a claim beside its own successor is
    #     not a contradiction and filing it under that word is the misreading
    #     `recurs` exists to prevent. Only mixed pairs qualify: two active facts
    #     are redundancy, two historical ones are both past.
    async def _recurrences():
        return await _same_frame_pairs([
            pair for pair in nominated_pairs
            if {pair[0].status, pair[1].status}
            == {NodeStatus.ACTIVE, NodeStatus.HISTORICAL}
        ])

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
        similar_pairs = await phase("topic_consolidation", _consolidation, tokens=len)
        split_candidates = await phase("split_detection", _splits, tokens=len)
        enrichment_candidates = await phase("enrichment_scan", _enrichment, tokens=len)
        contradictions = await phase(
            "contradiction_detection", _contradictions, tokens=len
        )
        recurrences = await phase("recurrence_detection", _recurrences, tokens=len)
        pending_review = await phase("pending_review", _pending_review, tokens=len)
        archival_candidates = await phase(
            "archival_nomination", _archival, tokens=len
        )
        similar_relations = await phase(
            "relation_consolidation", _relations, tokens=len
        )

    result = {
        "similar_pairs": similar_pairs,
        "split_candidates": split_candidates,
        "enrichment_candidates": enrichment_candidates,
        "contradictions": contradictions,
        "recurrences": recurrences,
        "pending_review": pending_review,
        "archival_candidates": archival_candidates,
        "similar_relations": similar_relations,
    }
    meta = ResponseMeta(
        nodes_returned=(
            len(similar_pairs) + len(split_candidates)
            + len(enrichment_candidates) + len(contradictions)
            + len(recurrences) + len(pending_review) + len(archival_candidates)
            + len(similar_relations)
        ),
        # Reflect **scans** the whole active graph and the agent sees only the
        # nominees, so a reflect record dims everything except them. That is
        # accurate rather than a special case: `retrieved` is what the response
        # carried, never what the tool looked at (§2, corrected).
        retrieved=_declare(_ids_within(result)),
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
    judgments: list[dict] | None = None,
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
    supersessions: [{old_id: str, by_id: str, because: str}] — resolve a
        flagged/contested node (from reflect's pending_review) by superseding
        ``old_id`` with an existing node ``by_id``. Atomic: marks old superseded
        (lineage old → by), flags inferences that depended on old as
        evidence_stale, and clears any supersession candidacy on it. The winner
        is unchanged; no new node. ``because`` is required and is a judgment —
        ``"it_was_wrong"`` or ``"the_world_changed"``; if you cannot tell which
        happened, leave the pair contested rather than guessing (see `update`).
    archivals: [node_id] — archive the approved nodes from reflect's
        archival_candidates. Exports each node with its edges (returned as
        ``archive_data`` — keep it; that copy is the archive) and atomically
        flips them to ARCHIVED, which removes them from every active-status
        query. Nothing is deleted, and ``restore`` reverses it. Unknown or
        already-retired ids are skipped, as supersessions are.
    judgments: [{node_id: str, direction: "up"|"down", reason: str,
        related_id: str | None}] — re-judge a node's importance, typically a
        `stale_judgment` nominee from reflect's archival_candidates. The verdict
        that has no other expression: "keep it, and stop treating it as
        important" — or, just as often, "still important, and now recently
        confirmed". Either way the node leaves the stale set, because the clock
        moves whichever direction the judgment goes. Unknown ids are skipped, as
        supersessions and archivals are.
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
        # Enrichment rewrites a topic's own description; the earlier wording
        # was never true-of-a-period, so this is a correction (#53).
        await supersede_node(
            old_topic, enriched, storage, embedding_provider,
            status=NodeStatus.CORRECTED,
        )
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

        # Combined in one shared place: a field-by-field rebuild here silently
        # reset both value clocks, leaving merged nodes permanently exempt from
        # archival nomination (#45).
        merged_value = merged_value_signal([s.value for s in sources])
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
        await supersede_by_existing(
            old_node, by_id, storage,
            status=superseded_status_for(supersede_spec["because"]),
        )
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
            at=datetime.now(timezone.utc),
        )

    # 7. Re-judge importance. Separate from archivals on purpose: archiving is a
    #    status verdict wanting human approval, while a change of degree is
    #    something the agent may conclude on its own.
    judgments_applied = 0
    for spec in (judgments or []):
        try:
            await judge_importance(
                spec["node_id"],
                direction=spec["direction"],
                reason=spec["reason"],
                storage=storage,
                related_id=spec.get("related_id"),
            )
        except ValueError:
            continue        # unknown node or related id — skipped, as above
        judgments_applied += 1

    # 8. Consolidate synonymous user relationship labels: relabel edges in place
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
        "judgments_applied": judgments_applied,
        "relations_consolidated": relations_consolidated,
        "edges_relabeled": edges_relabeled,
    }
    meta = ResponseMeta(
        nodes_returned=(
            parents_created + topics_split + topics_enriched
            + topics_merged + supersessions_applied + len(to_archive)
            + judgments_applied + relations_consolidated
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
    from epimemer.pipelines.reflection.review import review_labels_for

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

    review_by_node = await review_labels_for(nodes, storage)
    nodes_data = []
    for node in nodes:
        node_dict = _node_to_dict(node)
        if node.id in review_by_node:
            node_dict["review"] = review_by_node[node.id]
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
        # Everything but the seed arrived by walking edges from it, which is
        # what `expanded` means; the seed itself was asked for by id.
        retrieved=_declare(
            (n.id for n in nodes),
            provenance={
                n.id: (
                    SeedProvenance.DIRECT
                    if n.id == seed_node.id
                    else SeedProvenance.EXPANDED
                )
                for n in nodes
            },
        ),
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
    storage: StorageBackend,
    *,
    archive_data: dict | None = None,
    node_ids: list[str] | None = None,
    sourced_from: str | None = None,
    validity: list[dict] | None = None,
) -> tuple[dict, ResponseMeta]:
    """Bring nodes back: from an archive blob, or by id when a claim recurs.

    Three shapes reach this, and they need different writes.

    A *cold-storage reimport* brings back records the graph no longer holds:
    those are reconstructed first (so a malformed record fails before anything
    is written) and persisted in a single ``write_batch_tx`` — all of it lands
    or none of it does.

    An *un-archival* is the reversal of the hygiene sweep, and there the rows
    are still present: `archive` never deletes, it flips status. Re-inserting
    them would write nothing, so anything already stored as ARCHIVED is flipped
    back to ACTIVE instead.

    A *reactivation* names `node_ids` directly: a claim retired as HISTORICAL
    because the world moved on, asserted true again by a new source (#53 T2).
    Labour out of government in 2010 and back in 2024 is one claim recurring,
    not two claims, and the alternative — a second node saying what the first
    one said — is the duplication this graph exists to avoid, manufactured by
    its own bookkeeping.

    **What may come back is `RESTORABLE_STATUSES`, and CORRECTED is not in it.**
    That was always this tool's stated reason — *restoring an archive must not
    resurrect a node that was superseded for being wrong* — but before the
    status split it could only be enforced as "not superseded", which refused
    the world-change case too. Now it says what it means.

    **A reactivation must name the source asserting the claim again**, and the
    flip and that edge land in one transaction. A node back to ACTIVE with no
    edge recording why is an assertion the graph makes and cannot attribute.
    The prior intervals and the `temporally_followed_by` record are untouched,
    so the node ends holding several disjoint periods — which is what a list of
    intervals was for.
    """
    archive_data = archive_data or {}
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

    reactivated, new_edges = await _reactivation(
        node_ids or [], sourced_from, validity, storage
    )

    # Only edges reaching a node that was itself missing can be missing: an
    # edge between two nodes still in the graph was never removed.
    missing_ids = {node.id for node in missing}
    missing_edges = [
        edge for edge in edges
        if edge.src_id in missing_ids or edge.dst_id in missing_ids
    ]

    await storage.write_batch_tx(nodes=missing, edges=missing_edges)
    coming_back = archived + reactivated
    if coming_back:
        await storage.set_node_status_tx(
            coming_back, status=NodeStatus.ACTIVE,
            at=datetime.now(timezone.utc), edges=new_edges,
        )

    result = {
        "nodes_restored": len(missing),
        "nodes_reactivated": len(coming_back),
        "edges_restored": len(missing_edges) + len(new_edges),
    }
    meta = ResponseMeta(nodes_returned=len(missing) + len(coming_back))
    return result, meta


async def _reactivation(
    node_ids: list[str],
    sourced_from: str | None,
    validity: list[dict] | None,
    storage: StorageBackend,
) -> tuple[list[EpistemicNode], list[NodeEdge]]:
    """The nodes a `recurs` verdict brings back, and the provenance it brings.

    Every refusal here is checked before anything is written, so a batch naming
    one CORRECTED node changes nothing at all rather than reactivating the rest
    and reporting an error about the one.
    """
    if not node_ids:
        return [], []

    nodes: list[EpistemicNode] = []
    for node_id in node_ids:
        node = await storage.get_node(node_id)
        if node is None:
            raise ValueError(f"Node '{node_id}' not found.")
        if node.status is NodeStatus.ACTIVE:
            continue  # already back; asking twice is not an error
        if node.status not in RESTORABLE_STATUSES:
            raise ValueError(
                f"'{node_id}' is {node.status.value} and cannot be restored. A "
                f"claim retired for being wrong has no route back — supersede "
                f"the correction instead if the graph now says otherwise. "
                f"Restorable: "
                f"{', '.join(sorted(s.value for s in RESTORABLE_STATUSES))}."
            )
        nodes.append(node)

    if not nodes:
        return [], []

    historical = [n for n in nodes if n.status is NodeStatus.HISTORICAL]
    if historical and sourced_from is None:
        raise ValueError(
            "reactivating a historical claim requires `sourced_from`: the "
            "document asserting it is true again. Without it the graph would "
            "state the claim and be unable to say who says so."
        )
    if sourced_from is not None and await storage.get_document(sourced_from) is None:
        raise ValueError(f"Document '{sourced_from}' not found.")

    intervals = [ValidityInterval.model_validate(v) for v in (validity or [])]
    edges = [
        NodeEdge(
            src_id=node.id, dst_id=sourced_from, type=EdgeType.SOURCED_FROM,
            validity=intervals,
        )
        for node in nodes
        if sourced_from is not None
    ]
    return nodes, edges


# --- Helpers ---


def _node_to_dict(node: EpistemicNode) -> dict:
    """Serialize a node to dict with its type tag.

    `value.confidence` goes out as `null` when nobody rated the node, and is
    deliberately not substituted with the 0.5 that `rated_confidence` supplies
    elsewhere. This is the surface an agent reads, and it is the audience the
    nullable field exists for: "no one has assessed this" is worth knowing when
    deciding how far to lean on a retrieved claim, and 0.5 cannot say it (#46).
    """
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


async def _metacontext_labels_for(
    node_ids: Sequence[str], storage: StorageBackend
) -> dict[str, list[str]]:
    """`_metacontext_labels` for many nodes at once, keyed by node id.

    Each metacontext is read once for the whole set rather than once per node
    that carries it — a shared frame is the normal case, so per node meant
    re-reading the same handful of records for every result.
    """
    tagged = await storage.get_edges_for(
        node_ids, direction="from", edge_type=EdgeType.HAS_METACONTEXT
    )
    contents: dict[str, str] = {}
    for edges in tagged.values():
        for edge in edges:
            if edge.dst_id not in contents:
                mc = await storage.get_metacontext(edge.dst_id)
                if mc:
                    contents[edge.dst_id] = mc.content
    return {
        node_id: [contents[e.dst_id] for e in edges if e.dst_id in contents]
        for node_id, edges in tagged.items()
    }


async def _metacontext_labels(node_id: str, storage: StorageBackend) -> list[str]:
    """Content labels of the metacontexts a node is tagged with."""
    return (await _metacontext_labels_for([node_id], storage))[node_id]


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
    meta = ResponseMeta(nodes_returned=1, retrieved=_declare([node_id]))
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
    meta = ResponseMeta(
        nodes_returned=len(metacontexts), retrieved=_declare([node_id])
    )
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
