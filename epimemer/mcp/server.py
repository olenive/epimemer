"""FastMCP server for the Epimemer memory system.

Exposes memory tools (ingest, search, link, update, reflect,
query_graph, archive, restore) via the Model Context Protocol.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastmcp import Context, FastMCP

from epimemer.logging.structured import ToolInvocationLog, log_tool_call, setup_logging
from epimemer.mcp import tools
from epimemer.mcp.config import (
    create_embedding_provider,
    create_storage,
    load_config,
)
from epimemer.mcp.types import ResponseMeta, ToolResponse


def _parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 string to a tz-aware UTC datetime.

    Naive inputs are assumed to be UTC; offset-aware inputs are converted. This
    keeps every timestamp uniform UTC, which the storage temporal comparisons
    (lexicographic on ISO strings) rely on for correctness.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_windows(
    now: datetime,
    *,
    last_hours: float | None = None,
    last_days: float | None = None,
    windows: list[list[str]] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Resolve the ergonomic query_changes args into concrete UTC windows.

    Precedence: explicit `windows` (a missing/empty end means `now`) >
    `last_hours`/`last_days` trailing window > default last 24h. Every bound is
    normalized to UTC and each window is validated as start < end.
    """
    resolved: list[tuple[datetime, datetime]] = []
    if windows:
        for w in windows:
            start = _parse_utc(w[0])
            end = _parse_utc(w[1]) if len(w) > 1 and w[1] else now
            resolved.append((start, end))
    elif last_hours is not None or last_days is not None:
        delta = timedelta(hours=last_hours or 0, days=last_days or 0)
        resolved.append((now - delta, now))
    else:
        resolved.append((now - timedelta(hours=24), now))

    for start, end in resolved:
        if start >= end:
            raise ValueError(
                f"window start {start.isoformat()} must be before end {end.isoformat()}"
            )
    return resolved


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Initialize providers and yield them as lifespan context."""
    config = load_config()
    setup_logging(config.log_level, config.log_file)

    storage = create_storage(config)

    # Every backend implements connect (no-op where there's nothing to open).
    await storage.connect()

    embedding_provider = create_embedding_provider(config)

    # Optional: publish visualization events to the standalone hub. This process
    # never binds the viz port itself — it dials out to the hub (auto-spawning one
    # if none is running), so stale MCP orphans become dead sessions rather than a
    # stray server answering on the port with the wrong graph.
    stop_viz_client = None
    event_bus = None
    viz_session = None
    viz_hub_url = None
    if config.viz_enabled:
        from epimemer.visualization.event_bus import create_event_bus
        from epimemer.visualization.hub import ensure_hub_running
        from epimemer.visualization.hub_client import start_hub_client
        from epimemer.visualization.instrumented_storage import instrument_storage
        from epimemer.visualization.protocol import SessionInfo

        logger = logging.getLogger(__name__)
        event_bus = create_event_bus()
        raw_storage = storage  # pre-instrumentation, for viz snapshot reads
        storage = instrument_storage(
            storage, event_bus, default_threshold=config.reflect_threshold
        )

        viz_session = SessionInfo(
            session_id=uuid4().hex,
            pid=os.getpid(),
            backend=raw_storage.backend_name,
            active_graph=raw_storage.current_database,
        )
        viz_hub_url = f"http://{config.viz_host}:{config.viz_port}"
        ingest_url = f"ws://{config.viz_host}:{config.viz_port}/ingest"

        reachable = await ensure_hub_running(
            config.viz_host, config.viz_port, autospawn=config.viz_autospawn
        )
        if not reachable:
            logger.warning(
                "Visualization hub not reachable at %s and not spawned "
                "(autospawn=%s). Publishing will retry in the background.",
                viz_hub_url,
                config.viz_autospawn,
            )
        stop_viz_client = await start_hub_client(
            event_bus,
            raw_storage,
            viz_session,
            ingest_url,
            default_reflect_threshold=config.reflect_threshold,
        )
        logger.info(
            "Visualization: publishing to hub at %s (session %s)",
            viz_hub_url,
            viz_session.session_id,
        )

    try:
        yield {
            "storage": storage,
            "embedding_provider": embedding_provider,
            "config": config,
            "event_bus": event_bus,
            "viz_session": viz_session,
            "viz_hub_url": viz_hub_url,
        }
    finally:
        if stop_viz_client is not None:
            await stop_viz_client()
        await storage.close()


mcp = FastMCP(
    "epimemer",
    instructions="Epimemer is a layered epistemic memory system. Use segment to segment text, "
    "then store_decomposition to store your extracted topics/facts/inferences. "
    "Use search to find relevant knowledge, reflect to consolidate the graph, "
    "and query_graph to explore relationships. "
    "Use list_graphs and use_graph to manage knowledge graphs.",
    lifespan=app_lifespan,
)


def _build_response(result: dict, meta: ResponseMeta, latency_ms: float) -> str:
    """Build a JSON-serialized ToolResponse with timing."""
    meta.latency_ms = latency_ms
    response = ToolResponse(result=result, meta=meta)
    return response.model_dump_json(by_alias=True)


def _log(
    tool_name: str,
    input_summary: str,
    output_summary: str,
    meta: ResponseMeta,
    error: str | None = None,
) -> None:
    """Emit a structured log entry for a tool call."""
    log_tool_call(ToolInvocationLog(
        tool_name=tool_name,
        timestamp=datetime.now(timezone.utc),
        input_summary=input_summary,
        output_summary=output_summary,
        latency_ms=meta.latency_ms,
        nodes_touched=meta.nodes_returned,
        error=error,
    ))


def _error_response(error: str) -> str:
    """Build a JSON error response."""
    meta = ResponseMeta()
    return json.dumps({"error": error, "_meta": meta.model_dump()})


_tool_logger = logging.getLogger("epimemer.mcp.tools")


async def _run_with_timeout(
    tool_name: str,
    coro: Callable[[], Awaitable[tuple[dict, ResponseMeta]]],
    ctx: Context,
    input_summary: str,
    output_summary_fn: Callable[[dict, ResponseMeta], str],
) -> str:
    """Run a tool coroutine with timeout, logging, and error handling."""
    deps = ctx.lifespan_context
    timeout = deps["config"].tool_timeout_seconds
    start = time.monotonic()
    try:
        result, meta = await asyncio.wait_for(coro(), timeout=timeout)
        latency = (time.monotonic() - start) * 1000
        _log(tool_name, input_summary, output_summary_fn(result, meta), meta)
        return _build_response(result, meta, latency)
    except asyncio.TimeoutError:
        latency = (time.monotonic() - start) * 1000
        error_msg = f"{tool_name} timed out after {timeout}s"
        _tool_logger.error(error_msg)
        meta = ResponseMeta(latency_ms=latency)
        _log(tool_name, input_summary, "TIMEOUT", meta, error=error_msg)
        return _error_response(error_msg)
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        error_msg = f"{tool_name} failed: {e}"
        _tool_logger.exception(error_msg)
        meta = ResponseMeta(latency_ms=latency)
        _log(tool_name, input_summary, "ERROR", meta, error=str(e))
        return _error_response(str(e))


# --- Tools ---


@mcp.tool(name="segment")
async def memory_segment(
    content: str,
    ctx: Context,
    source: str | None = None,
    source_type: str | None = None,
    published_by: str | None = None,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
) -> str:
    """Segment text and store the document. Returns segment IDs for you to decompose.

    This is step 1 of the two-step ingest flow. Each segment includes an ID and
    char_count. Use your copy of the original text to extract topics, facts,
    and inferences for each segment, then call store_decomposition.

    Topics: distinct themes discussed (1-5 sentence descriptions).
    Facts: atomic, verifiable, grounded statements.
    Inferences: higher-level interpretive derivations (explicitly provisional).

    Args:
        content: The text to segment and store.
        source: Name of the originating document, e.g. "ISSUES.md", "stripe-api",
            "chat#4012". Every node decomposed from it gets a `sourced_from` edge
            to this document.
        source_type: Free label for the kind of source (suggested: document,
            api, chat).
        published_by: Optional publishing/authoring entity (e.g. "BBC"). Resolved-
            or-created as an entity Topic and linked to the document.
        metadata: Optional metadata to attach to the document.
        segmentation_strategy: "paragraph" or "semantic". Uses server default if omitted.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.segment",
        lambda: tools.segment_text(
            content=content,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            config=deps["config"],
            source=source,
            source_type=source_type,
            published_by=published_by,
            metadata=metadata,
            segmentation_strategy=segmentation_strategy,
            event_bus=deps.get("event_bus"),
        ),
        ctx,
        f"content_length={len(content)}",
        lambda r, m: f"segments={len(r['segments'])}",
    )


@mcp.tool(name="store_decomposition")
async def memory_store_decomposition(
    document_id: str,
    segments: list[dict],
    ctx: Context,
    metacontext_id: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Store your decomposition of segments into topics, facts, and inferences.

    This is step 2 of the two-step ingest flow. Call this after segment
    with your extracted nodes.

    The response includes stores_since_reflect and reflect_threshold. When
    reflect_suggested is true, suggest running reflect to the user. The count
    belongs to the active graph, so it accumulates across reconnects and follows
    a use_graph switch.

    Args:
        document_id: The document ID returned by segment.
        segments: List of decomposed segments. Each entry:
            segment_id: str — from segment result
            topics/facts/inferences: each item is either a content string, or
              an object {"content": str, "tags": ["billing", ...],
              "importance": 0.8} to attach per-node tags and an optional
              importance prior (0.0–1.0, default 0.5). Set it only when you
              already know a node is unusually consequential or unusually
              disposable — importance is properly judged at reflect time, and
              `reinforce` is how it rises later.
        metacontext_id: Optional metacontext ID — all nodes will inherit this.
        tags: Optional document-level tag names applied to every node. Each tag
            becomes (or reuses) a Topic linked by a tagged_with edge. Every node
            also gets a sourced_from edge to the document.
    """
    deps = ctx.lifespan_context

    async def _do() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.store_decomposition(
            document_id=document_id,
            segments=segments,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            metacontext_id=metacontext_id,
            tags=tags,
            event_bus=deps.get("event_bus"),
        )
        count = await deps["storage"].bump_reflect_counter()
        threshold = await tools.effective_reflect_threshold(
            deps["storage"], deps["config"].reflect_threshold
        )
        result["stores_since_reflect"] = count
        result["reflect_threshold"] = threshold
        if count >= threshold:
            result["reflect_suggested"] = True
        return result, meta

    return await _run_with_timeout(
        "epimemer.store_decomposition",
        _do,
        ctx,
        f"doc={document_id} segments={len(segments)}",
        lambda r, m: f"nodes={m.nodes_returned} edges={r['edges_created']} reflect={r['stores_since_reflect']}/{r['reflect_threshold']}",
    )



@mcp.tool(name="search")
async def memory_search(
    query: str,
    ctx: Context,
    k: int = 10,
    node_types: list[str] | None = None,
    graph_hops: int = 1,
    metacontext_id: str | None = None,
    cross_frame: bool = False,
) -> str:
    """Search the epistemic memory graph.

    Performs hybrid retrieval: vector similarity search followed by
    graph expansion to discover related nodes. Results always include
    metacontext labels and computed review labels (superseded_candidate /
    evidence_stale / contested) so you can see when a node may be outdated,
    have stale evidence, or be contested before relying on it.

    For provenance/topic listings (which nodes came from X / are about Y), use
    find_nodes, not search.

    Args:
        query: Natural language search query.
        k: Maximum number of vector search results.
        node_types: Filter to specific types: "topic", "fact", "inference".
        graph_hops: Number of graph traversal hops from vector results.
        metacontext_id: Optional — frame-scope results to this metacontext plus
            untagged base-reality nodes (other frames are excluded).
        cross_frame: Set true to ignore frame scoping and search across all
            metacontexts (opt-in; otherwise frames don't bleed together).
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.search",
        lambda: tools.search(
            query=query,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            k=k,
            node_types=node_types,
            graph_hops=graph_hops,
            metacontext_id=metacontext_id,
            cross_frame=cross_frame,
            reinforcement_boost=deps["config"].reinforcement_boost,
            event_bus=deps.get("event_bus"),
        ),
        ctx,
        f"query={query[:50]}",
        lambda r, m: f"nodes={m.nodes_returned}",
    )


@mcp.tool(name="link")
async def memory_link(
    src_id: str,
    dst_id: str,
    ctx: Context,
    edge_type: str | None = None,
    relation: str | None = None,
    kind: str = "relationship",
    weight: float = 1.0,
    metadata: dict | None = None,
) -> str:
    """Create an edge between two existing nodes.

    Give either a known engine `edge_type` or a free `relation` label (open
    vocabulary — anything you need, e.g. "refuted_in", "funded_by").

    Args:
        src_id: Source node ID.
        dst_id: Destination node ID.
        edge_type: A known engine edge type (e.g. "supports", "contradicts").
        relation: A free user-defined relationship label (creates a RELATED edge).
        kind: For a user relation — "relationship" (followed in retrieval) or
            "attribution" (where it came from / who said it; not followed). A
            label already in use reuses its kind (classified once per label).
        weight: Edge weight (default 1.0).
        metadata: Optional metadata for the edge.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.link",
        lambda: tools.link(
            src_id=src_id,
            dst_id=dst_id,
            storage=deps["storage"],
            edge_type=edge_type,
            relation=relation,
            kind=kind,
            weight=weight,
            metadata=metadata,
        ),
        ctx,
        f"{src_id}->{dst_id}:{relation or edge_type}",
        lambda r, m: f"edge={r['edge_id']}",
    )


@mcp.tool(name="update")
async def memory_update(
    node_id: str,
    new_content: str,
    ctx: Context,
) -> str:
    """Update a node by creating a new version (immutable history).

    The old node is marked as superseded; a new node is created with
    a superseded_by edge linking old to new.

    Args:
        node_id: ID of the node to update.
        new_content: The updated content.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.update",
        lambda: tools.update(
            node_id=node_id,
            new_content=new_content,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
        ),
        ctx,
        f"node={node_id}",
        lambda r, m: f"new={r['new_node_id']}",
    )


@mcp.tool(name="supersede_by")
async def memory_supersede_by(
    old_id: str,
    existing_id: str,
    ctx: Context,
) -> str:
    """Supersede a node by an already-existing node (resolve outdated/contradiction).

    Marks old_id superseded by existing_id (superseded_by edge), flags inferences
    that depended on old_id as evidence_stale, and clears any supersession
    candidacy on it. The existing node is unchanged. Use when the current truth
    is already in the graph; use `update` when you have new content.

    Args:
        old_id: The node being retired.
        existing_id: The existing node that supersedes it.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.supersede_by",
        lambda: tools.supersede_by(
            old_id=old_id,
            existing_id=existing_id,
            storage=deps["storage"],
        ),
        ctx,
        f"old={old_id} by={existing_id}",
        lambda r, m: f"superseded={r['superseded_id']}",
    )


@mcp.tool(name="reinforce")
async def memory_reinforce(
    node_id: str,
    reason: str,
    ctx: Context,
    related_id: str | None = None,
) -> str:
    """Record that a node matters more than its current importance says.

    Use when you learn something that raises an existing node's standing — new
    evidence supporting it, a decision that turned out to hinge on it, a fact
    that keeps proving load-bearing. Importance is what protects a node from
    the archival sweep; retrieval already maintains relevance on its own, so
    there is no need to reinforce a node merely because you read it.

    Each call raises importance asymptotically (repeated calls approach 1.0,
    they do not pin it) and appends the reason to the node's reinforcement
    trail — there is no way to set the number directly, because an
    unattributable judgment cannot be reviewed later.

    Args:
        node_id: The node whose importance rises.
        reason: Why it matters — this is read by whoever reviews the judgment.
        related_id: Optional — the node whose arrival triggered the
            reassessment.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.reinforce",
        lambda: tools.reinforce(
            node_id=node_id,
            reason=reason,
            storage=deps["storage"],
            related_id=related_id,
            importance_step=deps["config"].importance_step,
        ),
        ctx,
        f"node={node_id} related={related_id}",
        lambda r, m: f"importance={r['importance']:.3f} bumps={r['reinforcements']}",
    )


@mcp.tool(name="check_conflicts")
async def memory_check_conflicts(
    fact_ids: list[str],
    ctx: Context,
    threshold: float = 0.83,
    k: int = 5,
) -> str:
    """Find existing facts that may conflict with the given facts (you then judge).

    Recall stage of the review loop: for each fact, returns similar active facts
    above `threshold` with their similarity score, metacontext labels, and a
    same_frame flag. Similarity only nominates — classify each candidate yourself
    (redundant / supersedes / contradicts / cross-frame / compatible) and record
    the verdict with supersede_by, record_contradiction, or record_variant. Run
    this on freshly-ingested fact ids to catch outdated or conflicting knowledge.

    Args:
        fact_ids: Fact node ids to check (e.g. facts just stored).
        threshold: Minimum cosine similarity for a candidate (default 0.83, high).
        k: Max candidates returned per fact.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.check_conflicts",
        lambda: tools.check_conflicts(
            fact_ids=fact_ids,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            threshold=threshold,
            k=k,
        ),
        ctx,
        f"facts={len(fact_ids)} threshold={threshold}",
        lambda r, m: f"candidates={m.nodes_returned}",
    )


@mcp.tool(name="record_contradiction")
async def memory_record_contradiction(
    a_id: str,
    b_id: str,
    ctx: Context,
) -> str:
    """Record a genuine contradiction between two facts (both stay active).

    Creates one `contradiction` edge (idempotent per pair). The response includes
    notify_user — when true (a same-frame contradiction), surface it to the user
    in conversation and ask how to resolve it. If the facts are in different
    frames it is not a real contradiction; prefer record_variant.

    Args:
        a_id: One fact id.
        b_id: The other fact id.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.record_contradiction",
        lambda: tools.record_contradiction(
            a_id=a_id,
            b_id=b_id,
            storage=deps["storage"],
        ),
        ctx,
        f"{a_id}<->{b_id}",
        lambda r, m: f"created={r['created']} notify={r['notify_user']}",
    )


@mcp.tool(name="record_variant")
async def memory_record_variant(
    a_id: str,
    b_id: str,
    ctx: Context,
) -> str:
    """Record that two facts are the same proposition resolved differently per frame.

    Creates one `variant_of` edge (idempotent per pair) so a cross-frame
    divergence (e.g. real history vs. a fiction frame) is queryable. Both facts
    stay active. Use for facts in different metacontexts; if they share a frame
    and conflict, use record_contradiction instead.

    Args:
        a_id: One fact id.
        b_id: The other fact id.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.record_variant",
        lambda: tools.record_variant(
            a_id=a_id,
            b_id=b_id,
            storage=deps["storage"],
        ),
        ctx,
        f"{a_id}<->{b_id}",
        lambda r, m: f"created={r['created']}",
    )


@mcp.tool(name="reflect")
async def memory_reflect(
    ctx: Context,
    similarity_threshold: float = 0.85,
    decay_rate: float = 0.05,
    relation_similarity_threshold: float = 0.9,
) -> str:
    """Analyse the memory graph and return candidates for you to act on.

    Applies value decay immediately, then identifies:
    - Similar topic pairs that could be consolidated under a parent (this also
      covers duplicate source/tag/entity Topics)
    - Topics with high internal variance that could be split
    - Topics with thin descriptions but rich associated material
    - Potential contradictions between facts (same-frame only)
    - pending_review: active nodes already flagged for resolution
      (superseded_candidate / evidence_stale / contested), with the related
      ids to act on via apply_reflection supersessions / supersede_by
    - similar_relations: likely-synonymous user relationship labels to consolidate
      via apply_reflection relation_merges

    Review the candidates and call apply_reflection with your decisions.

    For large graphs, consider delegating this to a subagent so analysis
    and decision-making don't consume your main conversation context.

    Args:
        similarity_threshold: Cosine similarity threshold for finding similar pairs.
        decay_rate: Multiplicative decay factor for relevance.
        relation_similarity_threshold: Similarity bar for proposing relationship-
            label consolidations.
    """
    deps = ctx.lifespan_context
    # Read for the log line only; the authoritative value is what the reset
    # below clears, which also counts anything stored while reflect ran.
    stores_before = await deps["storage"].get_reflect_counter()

    async def _do() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.reflect(
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            similarity_threshold=similarity_threshold,
            decay_rate=decay_rate,
            relation_similarity_threshold=relation_similarity_threshold,
            event_bus=deps.get("event_bus"),
        )
        result["stores_since_last_reflect"] = await deps["storage"].reset_reflect_counter()
        return result, meta

    return await _run_with_timeout(
        "epimemer.reflect",
        _do,
        ctx,
        f"threshold={similarity_threshold} stores_since={stores_before}",
        lambda r, m: (
            f"decayed={r['nodes_decayed']} pairs={len(r['similar_pairs'])} "
            f"pending={len(r['pending_review'])} relations={len(r['similar_relations'])}"
        ),
    )


@mcp.tool(name="apply_reflection")
async def memory_apply_reflection(
    ctx: Context,
    parents: list[dict] | None = None,
    splits: list[dict] | None = None,
    enrichments: list[dict] | None = None,
    merges: list[dict] | None = None,
    supersessions: list[dict] | None = None,
    archivals: list[str] | None = None,
    relation_merges: list[dict] | None = None,
    merge_similarity_threshold: float = 0.92,
) -> str:
    """Apply your reflection decisions to the memory graph.

    Call this after reviewing reflect results. All arguments optional.

    Args:
        parents: Consolidate similar topics under a new parent (non-destructive;
            children stay active). Each: {children_ids: [str], content: str}
            content = your synthesized parent description.
        splits: Split a broad topic into subtopics.
            Each: {topic_id: str, subtopics: [str]}
            subtopics = list of subtopic description strings.
        enrichments: Improve a topic's description using its associated material.
            Each: {topic_id: str, new_content: str}
        merges: Fuse near-duplicate topics into one combined topic; the sources
            are retired as MERGED history. Each: {source_ids: [str], content: str}.
            A merge is applied only if every pair of sources is at least
            merge_similarity_threshold similar, else it is rejected — use this
            only for true duplicates, and `parents` for merely related topics.
        supersessions: Resolve flagged/contested nodes from reflect's
            pending_review by superseding the outdated/losing node with an
            existing one. Each: {old_id: str, by_id: str}. Atomic; the winner is
            unchanged and dependent inferences are flagged evidence_stale.
        archivals: Archive trivial nodes from reflect's archival_candidates, as
            a list of node ids. **Ask the user before passing anything here** —
            archival is a human-approved verdict, like resolving a
            contradiction. Nothing is deleted: the response carries an
            archive_data export, and `restore` puts a node back. Never archive
            an inference on its own initiative; a flagged one means its evidence
            changed, which is a reason to re-derive it.
        relation_merges: Consolidate synonymous user relationship labels from
            reflect's similar_relations. Each: {labels: [str], into: str}. Every
            user-tier edge with a listed label is relabelled to `into`, in place.
        merge_similarity_threshold: Minimum pairwise cosine similarity required
            to allow a merge (default 0.92, deliberately high).
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.apply_reflection",
        lambda: tools.apply_reflection(
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            parents=parents,
            splits=splits,
            enrichments=enrichments,
            merges=merges,
            supersessions=supersessions,
            archivals=archivals,
            relation_merges=relation_merges,
            merge_similarity_threshold=merge_similarity_threshold,
        ),
        ctx,
        f"parents={len(parents or [])} splits={len(splits or [])} "
        f"enrichments={len(enrichments or [])} merges={len(merges or [])} "
        f"supersessions={len(supersessions or [])} archivals={len(archivals or [])} "
        f"relation_merges={len(relation_merges or [])}",
        lambda r, m: f"applied={m.nodes_returned}",
    )


@mcp.tool(name="query_graph")
async def memory_query_graph(
    node_id: str,
    ctx: Context,
    hops: int = 1,
    edge_types: list[str] | None = None,
) -> str:
    """Traverse the graph from a node, returning the local subgraph.

    Args:
        node_id: Starting node ID.
        hops: Number of traversal hops (default 1).
        edge_types: If provided, only traverse these edge types.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.query_graph",
        lambda: tools.query_graph(
            node_id=node_id,
            storage=deps["storage"],
            hops=hops,
            edge_types=edge_types,
        ),
        ctx,
        f"node={node_id} hops={hops}",
        lambda r, m: f"nodes={m.nodes_returned}",
    )


@mcp.tool(name="topic_tree")
async def memory_topic_tree(
    topic_id: str,
    ctx: Context,
    depth: int = 2,
) -> str:
    """Drill into a topic hierarchy: its ancestors and its subtopics.

    Returns ids and short content previews only — never the underlying
    material — so you can pick a branch and then search or query_graph just
    that. Use it when search returns a topic carrying `subtopics`, or to see
    how a broad topic was split.

    Args:
        topic_id: The topic to centre the tree on.
        depth: Levels of subtopics to descend (default 2; 1 is direct
            subtopics only). A subtopic cut off by the limit that has children
            of its own is flagged `has_more`.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.topic_tree",
        lambda: tools.topic_tree(
            topic_id=topic_id,
            storage=deps["storage"],
            depth=depth,
        ),
        ctx,
        f"topic={topic_id} depth={depth}",
        lambda r, m: (
            f"ancestors={len(r['ancestors'])} subtopics={len(r['subtopics'])}"
        ),
    )


@mcp.tool(name="as_of")
async def memory_as_of(
    at: str,
    ctx: Context,
    node_types: list[str] | None = None,
) -> str:
    """Snapshot the active knowledge set as it stood at a past instant.

    Returns the nodes that existed and were still active at `at` (an ISO
    datetime, normalized to UTC). This is a node-lifecycle snapshot only — edges,
    metacontext, and review labels are not time-versioned and are omitted, since
    they would reflect the present graph rather than the graph at `at`. For the
    *changes* across a span (births + retirements), use query_changes instead.

    Args:
        at: ISO datetime to snapshot at.
        node_types: Optional filter to "topic"/"fact"/"inference".
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.as_of",
        lambda: tools.as_of(
            at=_parse_utc(at),
            storage=deps["storage"],
            node_types=node_types,
        ),
        ctx,
        f"at={at}",
        lambda r, m: f"nodes={m.nodes_returned}",
    )


@mcp.tool(name="query_changes")
async def memory_query_changes(
    ctx: Context,
    last_hours: float | None = None,
    last_days: float | None = None,
    windows: list[list[str]] | None = None,
    node_types: list[str] | None = None,
) -> str:
    """What changed (births + retirements) in one or more time windows.

    Returns nodes whose creation or retirement fell inside each half-open window
    [start, end), each tagged with the lifecycle event(s) and enriched with
    metacontext/review labels. Distinct from `as_of`, which snapshots state at a
    single instant; this reports the *deltas* across a span.

    Specify windows one of three ways (precedence in this order):
      - windows: explicit [[startISO, endISO], ...]; a missing/empty end means now.
      - last_hours / last_days: a single trailing window ending now.
      - nothing: defaults to the last 24 hours.

    All times are normalized to UTC. node_types optionally filters to
    "topic"/"fact"/"inference".
    """
    deps = ctx.lifespan_context
    resolved = _resolve_windows(
        datetime.now(timezone.utc),
        last_hours=last_hours,
        last_days=last_days,
        windows=windows,
    )

    return await _run_with_timeout(
        "epimemer.query_changes",
        lambda: tools.query_changes(
            windows=resolved,
            storage=deps["storage"],
            node_types=node_types,
        ),
        ctx,
        f"windows={len(resolved)}",
        lambda r, m: f"changes={m.nodes_returned}",
    )


@mcp.tool(name="find_nodes")
async def memory_find_nodes(
    ctx: Context,
    sourced_from: str | None = None,
    tagged_with: str | None = None,
    node_types: list[str] | None = None,
    status: str = "active",
    limit: int = 50,
) -> str:
    """Find nodes connected to a source or topic hub by graph traversal.

    Unlike search (vector similarity), this returns exactly the nodes linked to a
    hub — e.g. find_nodes(sourced_from="ISSUES.md") returns everything that came
    from that document; find_nodes(tagged_with="billing") returns nodes about
    billing.

    Args:
        sourced_from: A document/entity id or name — return its `sourced_from` nodes.
        tagged_with: A Topic id or name — return nodes tagged with that concept.
        node_types: Filter to "topic"/"fact"/"inference".
        status: Node status to list (default "active").
        limit: Maximum nodes to return.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.find_nodes",
        lambda: tools.find_nodes(
            storage=deps["storage"],
            sourced_from=sourced_from,
            tagged_with=tagged_with,
            node_types=node_types,
            status=status,
            limit=limit,
        ),
        ctx,
        f"sourced_from={sourced_from} tagged_with={tagged_with}",
        lambda r, m: f"nodes={m.nodes_returned}",
    )


@mcp.tool(name="list_sources")
async def memory_list_sources(
    ctx: Context,
) -> str:
    """Discover the distinct source/origin nodes in the active graph.

    Returns the documents nodes are `sourced_from`, plus publishing entities,
    each with how many nodes reference it — so you can see what exists before
    find_nodes(sourced_from=...).
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.list_sources",
        lambda: tools.list_sources(storage=deps["storage"]),
        ctx,
        "",
        lambda r, m: f"sources={len(r['sources'])}",
    )


@mcp.tool(name="list_relations")
async def memory_list_relations(
    ctx: Context,
) -> str:
    """Discover the distinct user-defined relationship labels in the active graph.

    Returns each label with its kind (relationship/attribution) and usage count —
    useful before coining a new label or proposing consolidations.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.list_relations",
        lambda: tools.list_relations(storage=deps["storage"]),
        ctx,
        "",
        lambda r, m: f"relations={len(r['relations'])}",
    )


@mcp.tool(name="archive")
async def memory_archive(
    ctx: Context,
    max_age_days: int = 90,
) -> str:
    """Find and export old superseded/merged nodes for cold storage.

    Does NOT delete nodes — only exports them in a serializable format.

    Args:
        max_age_days: Minimum days since supersession/merge for archival.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.archive",
        lambda: tools.archive(
            storage=deps["storage"],
            max_age_days=max_age_days,
        ),
        ctx,
        f"max_age={max_age_days}d",
        lambda r, m: f"archived={r['nodes_archived']}",
    )


@mcp.tool(name="restore")
async def memory_restore(
    archive_data: dict,
    ctx: Context,
) -> str:
    """Restore previously archived nodes and edges into the graph.

    Args:
        archive_data: The archive dict (as returned by archive).
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.restore",
        lambda: tools.restore(
            archive_data=archive_data,
            storage=deps["storage"],
        ),
        ctx,
        f"nodes={len(archive_data.get('nodes', []))}",
        lambda r, m: f"restored={r['nodes_restored']}",
    )


# --- Timeline tools ---


@mcp.tool(name="create_timeline")
async def memory_create_timeline(
    name: str,
    ctx: Context,
    description: str = "",
) -> str:
    """Create a new timeline for tracking temporal relationships.

    Args:
        name: Name of the timeline (e.g., "History of AI").
        description: Optional description.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.create_timeline",
        lambda: tools.create_timeline(
            name=name,
            storage=deps["storage"],
            description=description,
        ),
        ctx,
        f"name={name}",
        lambda r, m: f"id={r['timeline_id']}",
    )


@mcp.tool(name="add_timepoint")
async def memory_add_timepoint(
    timeline_id: str,
    ctx: Context,
    start: str | None = None,
    end: str | None = None,
    label: str | None = None,
) -> str:
    """Add a timepoint to a timeline.

    Timepoints can be concrete (with start/end ISO datetimes), vague
    (label only), or a mix. Concrete timepoints are auto-sorted.

    Args:
        timeline_id: The timeline to add to.
        start: Optional ISO datetime string for the start.
        end: Optional ISO datetime string for the end (for intervals).
        label: Optional descriptive label (e.g., "during the Renaissance").
    """
    deps = ctx.lifespan_context
    parsed_start = _parse_utc(start) if start else None
    parsed_end = _parse_utc(end) if end else None
    return await _run_with_timeout(
        "epimemer.add_timepoint",
        lambda: tools.add_timeline_timepoint(
            timeline_id=timeline_id,
            storage=deps["storage"],
            start=parsed_start,
            end=parsed_end,
            label=label,
        ),
        ctx,
        f"timeline={timeline_id}",
        lambda r, m: f"tp={r['timepoint_id']}",
    )


@mcp.tool(name="query_timeline")
async def memory_query_timeline(
    timeline_id: str,
    ctx: Context,
    target: str | None = None,
    range_start: str | None = None,
    range_end: str | None = None,
    k: int = 5,
) -> str:
    """Query timepoints on a timeline.

    Either find nearest to a target datetime, or get all in a range.

    Args:
        timeline_id: The timeline to query.
        target: ISO datetime — find k nearest timepoints to this.
        range_start: ISO datetime — start of range query.
        range_end: ISO datetime — end of range query.
        k: Number of nearest results (default 5).
    """
    deps = ctx.lifespan_context
    parsed_target = _parse_utc(target) if target else None
    parsed_start = _parse_utc(range_start) if range_start else None
    parsed_end = _parse_utc(range_end) if range_end else None
    return await _run_with_timeout(
        "epimemer.query_timeline",
        lambda: tools.query_timeline(
            timeline_id=timeline_id,
            storage=deps["storage"],
            target=parsed_target,
            range_start=parsed_start,
            range_end=parsed_end,
            k=k,
        ),
        ctx,
        f"timeline={timeline_id}",
        lambda r, m: f"timepoints={m.nodes_returned}",
    )


@mcp.tool(name="create_timelink")
async def memory_create_timelink(
    node_id: str,
    timeline_id: str,
    timepoint_id: str,
    ctx: Context,
) -> str:
    """Link a node to a specific timepoint on a timeline.

    Args:
        node_id: The node to link.
        timeline_id: The timeline containing the timepoint.
        timepoint_id: The specific timepoint within the timeline.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.create_timelink",
        lambda: tools.create_timelink(
            node_id=node_id,
            timeline_id=timeline_id,
            timepoint_id=timepoint_id,
            storage=deps["storage"],
        ),
        ctx,
        f"{node_id}->{timeline_id}:{timepoint_id}",
        lambda r, m: f"edge={r['edge_id']}",
    )


# --- Metacontext tools ---


@mcp.tool(name="create_metacontext")
async def memory_create_metacontext(
    content: str,
    ctx: Context,
    description: str = "",
) -> str:
    """Create a new metacontext for epistemic framing.

    Metacontexts disambiguate different takes, sources, or interpretations.
    Examples: "Real historical events", "World of Darkness universe",
    "Reporting by the BBC".

    Args:
        content: Short name for the metacontext.
        description: Optional longer explanation.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.create_metacontext",
        lambda: tools.create_metacontext(
            content=content,
            storage=deps["storage"],
            description=description,
        ),
        ctx,
        f"content={content[:50]}",
        lambda r, m: f"id={r['metacontext_id']}",
    )


@mcp.tool(name="get_metacontexts")
async def memory_get_metacontexts(
    node_id: str,
    ctx: Context,
) -> str:
    """Get all metacontexts associated with a node.

    Args:
        node_id: The node to get metacontexts for.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.get_metacontexts",
        lambda: tools.get_metacontexts_for_node(
            node_id=node_id,
            storage=deps["storage"],
        ),
        ctx,
        f"node={node_id}",
        lambda r, m: f"metacontexts={m.nodes_returned}",
    )


# --- Graph management tools ---


@mcp.tool(name="graph_stats")
async def epimemer_graph_stats(
    ctx: Context,
) -> str:
    """Summary statistics for the active knowledge graph.

    Returns total node and edge counts, a breakdown by node type
    (topic/fact/inference) and edge type, and metacontext/timeline totals.
    Use this to gauge how much is stored before searching or reflecting.

    Also reports stores_since_reflect against reflect_threshold for the active
    graph. When reflect_suggested is true, suggest running reflect to the user.
    reflect_threshold_overridden says whether that threshold was set for this
    graph via configure_reflection or comes from the server default.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.graph_stats",
        lambda: tools.graph_stats(
            storage=deps["storage"],
            default_reflect_threshold=deps["config"].reflect_threshold,
        ),
        ctx,
        "",
        lambda r, m: (
            f"nodes={r['total_nodes']} edges={r['total_edges']} "
            f"mc={r['metacontexts']} graph={r['graph']} "
            f"reflect={r['stores_since_reflect']}/{r['reflect_threshold']}"
        ),
    )


@mcp.tool(name="configure_reflection")
async def epimemer_configure_reflection(
    ctx: Context,
    threshold: int | None = None,
) -> str:
    """Set how many stores this graph takes before a reflect is suggested.

    The setting belongs to the active graph and persists — use it when one
    graph deserves a different rhythm from the server default (a scratchpad
    that should consolidate often, a reference graph that should not).

    To reflect sooner, just call reflect; to defer, raise the threshold. There
    is deliberately no way to zero the counter without reflecting, which would
    throw the signal away rather than postpone it.

    Args:
        threshold: Stores before a reflect is suggested (at least 1). Omit it
            to clear this graph's setting and follow the server default again.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.configure_reflection",
        lambda: tools.configure_reflection(
            storage=deps["storage"],
            threshold=threshold,
            default_threshold=deps["config"].reflect_threshold,
        ),
        ctx,
        f"threshold={threshold}",
        lambda r, m: (
            f"graph={r['graph']} threshold={r['reflect_threshold']} "
            f"overridden={r['overridden']}"
        ),
    )


@mcp.tool(name="list_graphs")
async def epimemer_list_graphs(
    ctx: Context,
) -> str:
    """List available knowledge graphs and show which is active.

    Each graph is an isolated knowledge base. Use use_graph to switch.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.list_graphs",
        lambda: tools.list_graphs(storage=deps["storage"]),
        ctx,
        "",
        lambda r, m: f"graphs={len(r['graphs'])} active={r['active_graph']}",
    )


@mcp.tool(name="use_graph")
async def epimemer_use_graph(
    name: str,
    ctx: Context,
    confirm: bool = False,
) -> str:
    """Switch to a different knowledge graph, or create a new one.

    If the graph doesn't exist, returns a confirmation prompt with similar
    graph names in case of typos. Call again with confirm=true to create it.

    Args:
        name: Name of the graph to switch to (or create).
        confirm: Set to true to confirm creation of a new graph.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.use_graph",
        lambda: tools.use_graph(
            name=name,
            storage=deps["storage"],
            confirm=confirm,
        ),
        ctx,
        f"name={name} confirm={confirm}",
        lambda r, m: f"status={r.get('status', 'error')}",
    )


@mcp.tool(name="delete_graph")
async def epimemer_delete_graph(
    name: str,
    ctx: Context,
    confirm: bool = False,
) -> str:
    """Permanently delete a knowledge graph and all its data.

    Cannot delete the currently active graph — switch away first.
    Requires confirm=true to proceed.

    Args:
        name: Name of the graph to delete.
        confirm: Must be true to actually delete. False returns a confirmation prompt.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.delete_graph",
        lambda: tools.delete_graph(
            name=name,
            storage=deps["storage"],
            confirm=confirm,
        ),
        ctx,
        f"name={name} confirm={confirm}",
        lambda r, m: f"status={r.get('status', 'error')}",
    )


@mcp.tool(name="viz_status")
async def epimemer_viz_status(ctx: Context) -> str:
    """Report where this session publishes visualization events, and whether the
    hub can see it.

    The durable answer to "I opened the visualizer but can't find my graph": the
    returned session_id / backend / active_graph name the session to pick in the
    UI's session selector. Because this runs inside the very process you are
    driving, its answer is authoritative for *this* session.
    """
    deps = ctx.lifespan_context

    async def _do() -> tuple[dict, ResponseMeta]:
        session = deps.get("viz_session")
        hub_url = deps.get("viz_hub_url")
        if session is None or hub_url is None:
            return {"viz_enabled": False}, ResponseMeta()

        from urllib.parse import urlparse

        from epimemer.visualization.hub import hub_sessions, probe_health

        parsed = urlparse(hub_url)
        host, port = parsed.hostname, parsed.port
        health = await asyncio.to_thread(probe_health, host, port)
        sessions = await asyncio.to_thread(hub_sessions, host, port) if health else None

        connected = False
        for s in sessions or []:
            if s.get("session_id") == session.session_id:
                connected = bool(s.get("connected"))
                break

        result = {
            "viz_enabled": True,
            "hub_url": hub_url,
            "hub_reachable": health is not None,
            "connected": connected,
            "session_id": session.session_id,
            "backend": session.backend,
            "active_graph": deps["storage"].current_database,
            "sessions_on_hub": len(sessions) if sessions is not None else 0,
        }
        return result, ResponseMeta()

    return await _run_with_timeout(
        "epimemer.viz_status",
        _do,
        ctx,
        "",
        lambda r, m: f"reachable={r.get('hub_reachable')} connected={r.get('connected')}",
    )


if __name__ == "__main__":
    mcp.run()
