"""FastMCP server for the Epimemer memory system.

Exposes memory tools (ingest, search, link, update, reflect,
query_graph, archive, restore) via the Model Context Protocol.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastmcp import Context, FastMCP

from epimemer.logging.structured import ToolInvocationLog, log_tool_call, setup_logging
from epimemer.mcp import tools
from epimemer.mcp.config import (
    create_embedding_provider,
    create_storage,
    load_config,
)
from epimemer.mcp.types import ResponseMeta, ToolResponse


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Initialize providers and yield them as lifespan context."""
    config = load_config()
    setup_logging(config.log_level, config.log_file)

    storage = create_storage(config)

    # SurrealDB needs async connect
    if hasattr(storage, "connect"):
        await storage.connect()

    embedding_provider = create_embedding_provider(config)

    # Optional: visualization server with instrumented storage
    viz_server = None
    event_bus = None
    if config.viz_enabled:
        import asyncio
        import logging
        import uvicorn
        from epimemer.visualization.event_bus import create_event_bus
        from epimemer.visualization.instrumented_storage import instrument_storage
        from epimemer.visualization.ws_server import create_app

        event_bus = create_event_bus()
        storage = instrument_storage(storage, event_bus)

        viz_app = create_app(event_bus)
        viz_config = uvicorn.Config(
            viz_app,
            host=config.viz_host,
            port=config.viz_port,
            log_level="warning",
            lifespan="off",
        )
        viz_server = uvicorn.Server(viz_config)
        logger = logging.getLogger(__name__)

        async def _run_viz():
            try:
                await viz_server.serve()
            except SystemExit:
                logger.warning(
                    "Visualization server failed to start on %s:%d (port in use?). "
                    "Continuing without visualization.",
                    config.viz_host,
                    config.viz_port,
                )

        asyncio.create_task(_run_viz())
        logger.info(
            "Visualization server starting at http://%s:%d",
            config.viz_host,
            config.viz_port,
        )

    try:
        yield {
            "storage": storage,
            "embedding_provider": embedding_provider,
            "config": config,
            "event_bus": event_bus,
            "stores_since_reflect": 0,
        }
    finally:
        if viz_server is not None:
            viz_server.should_exit = True
        if hasattr(storage, "close"):
            await storage.close()


mcp = FastMCP(
    "epimemer",
    instructions="Epimemer is a layered epistemic memory system. Use memory.segment to segment text, "
    "then memory.store_decomposition to store your extracted topics/facts/inferences. "
    "Use memory.search to find relevant knowledge, memory.reflect to consolidate the graph, "
    "and memory.query_graph to explore relationships.",
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
        llm_calls=meta.llm_calls,
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


@mcp.tool(name="memory.segment")
async def memory_segment(
    content: str,
    ctx: Context,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
) -> str:
    """Segment text and store the document. Returns segment IDs for you to decompose.

    This is step 1 of the two-step ingest flow. Each segment includes an ID and
    char_count. Use your copy of the original text to extract topics, facts,
    and inferences for each segment, then call memory.store_decomposition.

    Topics: distinct themes discussed (1-5 sentence descriptions).
    Facts: atomic, verifiable, grounded statements.
    Inferences: higher-level interpretive derivations (explicitly provisional).

    Args:
        content: The text to segment and store.
        metadata: Optional metadata to attach to the document.
        segmentation_strategy: "paragraph" or "semantic". Uses server default if omitted.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "memory.segment",
        lambda: tools.segment_text(
            content=content,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            config=deps["config"],
            metadata=metadata,
            segmentation_strategy=segmentation_strategy,
            event_bus=deps.get("event_bus"),
        ),
        ctx,
        f"content_length={len(content)}",
        lambda r, m: f"segments={len(r['segments'])}",
    )


@mcp.tool(name="memory.store_decomposition")
async def memory_store_decomposition(
    document_id: str,
    segments: list[dict],
    ctx: Context,
    metacontext_id: str | None = None,
) -> str:
    """Store your decomposition of segments into topics, facts, and inferences.

    This is step 2 of the two-step ingest flow. Call this after memory.segment
    with your extracted nodes.

    The response includes stores_since_reflect and reflect_threshold. When
    reflect_suggested is true, suggest running memory.reflect to the user.

    Args:
        document_id: The document ID returned by memory.segment.
        segments: List of decomposed segments. Each entry:
            segment_id: str — from memory.segment result
            topics: list[str] — distinct themes (1-5 sentence descriptions)
            facts: list[str] — atomic, verifiable statements
            inferences: list[str] — provisional higher-level derivations
        metacontext_id: Optional metacontext ID — all nodes will inherit this.
    """
    deps = ctx.lifespan_context

    async def _do() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.store_decomposition(
            document_id=document_id,
            segments=segments,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            metacontext_id=metacontext_id,
            event_bus=deps.get("event_bus"),
        )
        deps["stores_since_reflect"] = deps.get("stores_since_reflect", 0) + 1
        threshold = deps["config"].reflect_threshold
        count = deps["stores_since_reflect"]
        result["stores_since_reflect"] = count
        result["reflect_threshold"] = threshold
        if count >= threshold:
            result["reflect_suggested"] = True
        return result, meta

    return await _run_with_timeout(
        "memory.store_decomposition",
        _do,
        ctx,
        f"doc={document_id} segments={len(segments)}",
        lambda r, m: f"nodes={m.nodes_returned} edges={r['edges_created']} reflect={r['stores_since_reflect']}/{r['reflect_threshold']}",
    )



@mcp.tool(name="memory.search")
async def memory_search(
    query: str,
    ctx: Context,
    k: int = 10,
    node_types: list[str] | None = None,
    graph_hops: int = 1,
    metacontext_id: str | None = None,
) -> str:
    """Search the epistemic memory graph.

    Performs hybrid retrieval: vector similarity search followed by
    graph expansion to discover related nodes. Results always include
    metacontext labels for epistemic clarity.

    Args:
        query: Natural language search query.
        k: Maximum number of vector search results.
        node_types: Filter to specific types: "topic", "fact", "inference".
        graph_hops: Number of graph traversal hops from vector results.
        metacontext_id: Optional — filter results to nodes with this metacontext.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "memory.search",
        lambda: tools.search(
            query=query,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            k=k,
            node_types=node_types,
            graph_hops=graph_hops,
            metacontext_id=metacontext_id,
            event_bus=deps.get("event_bus"),
        ),
        ctx,
        f"query={query[:50]}",
        lambda r, m: f"nodes={m.nodes_returned}",
    )


@mcp.tool(name="memory.link")
async def memory_link(
    src_id: str,
    dst_id: str,
    edge_type: str,
    ctx: Context,
    weight: float = 1.0,
    metadata: dict | None = None,
) -> str:
    """Create a typed edge between two existing nodes.

    Args:
        src_id: Source node ID.
        dst_id: Destination node ID.
        edge_type: Edge type (e.g., "supports", "contradicts", "derived_from").
        weight: Edge weight (default 1.0).
        metadata: Optional metadata for the edge.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "memory.link",
        lambda: tools.link(
            src_id=src_id,
            dst_id=dst_id,
            edge_type=edge_type,
            storage=deps["storage"],
            weight=weight,
            metadata=metadata,
        ),
        ctx,
        f"{src_id}->{dst_id}:{edge_type}",
        lambda r, m: f"edge={r['edge_id']}",
    )


@mcp.tool(name="memory.update")
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
        "memory.update",
        lambda: tools.update(
            node_id=node_id,
            new_content=new_content,
            storage=deps["storage"],
        ),
        ctx,
        f"node={node_id}",
        lambda r, m: f"new={r['new_node_id']}",
    )


@mcp.tool(name="memory.reflect")
async def memory_reflect(
    ctx: Context,
    similarity_threshold: float = 0.85,
    decay_rate: float = 0.05,
) -> str:
    """Analyse the memory graph and return candidates for you to act on.

    Applies value decay immediately, then identifies:
    - Similar topic pairs that could be consolidated under a parent
    - Topics with high internal variance that could be split
    - Topics with thin descriptions but rich associated material
    - Potential contradictions between facts

    Review the candidates and call memory.apply_reflection with your decisions.

    For large graphs, consider delegating this to a subagent so analysis
    and decision-making don't consume your main conversation context.

    Args:
        similarity_threshold: Cosine similarity threshold for finding similar pairs.
        decay_rate: Multiplicative decay factor for relevance.
    """
    deps = ctx.lifespan_context
    stores_before = deps.get("stores_since_reflect", 0)

    async def _do() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.reflect(
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            similarity_threshold=similarity_threshold,
            decay_rate=decay_rate,
            event_bus=deps.get("event_bus"),
        )
        deps["stores_since_reflect"] = 0
        result["stores_since_last_reflect"] = stores_before
        return result, meta

    return await _run_with_timeout(
        "memory.reflect",
        _do,
        ctx,
        f"threshold={similarity_threshold} stores_since={stores_before}",
        lambda r, m: f"decayed={r['nodes_decayed']} pairs={len(r['similar_pairs'])}",
    )


@mcp.tool(name="memory.apply_reflection")
async def memory_apply_reflection(
    ctx: Context,
    parents: list[dict] | None = None,
    splits: list[dict] | None = None,
    enrichments: list[dict] | None = None,
) -> str:
    """Apply your reflection decisions to the memory graph.

    Call this after reviewing memory.reflect results. All arguments optional.

    Args:
        parents: Consolidate similar topics under a new parent.
            Each: {children_ids: [str], content: str}
            content = your synthesized parent description.
        splits: Split a broad topic into subtopics.
            Each: {topic_id: str, subtopics: [str]}
            subtopics = list of subtopic description strings.
        enrichments: Improve a topic's description using its associated material.
            Each: {topic_id: str, new_content: str}
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "memory.apply_reflection",
        lambda: tools.apply_reflection(
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            parents=parents,
            splits=splits,
            enrichments=enrichments,
        ),
        ctx,
        f"parents={len(parents or [])} splits={len(splits or [])} enrichments={len(enrichments or [])}",
        lambda r, m: f"applied={m.nodes_returned}",
    )


@mcp.tool(name="memory.query_graph")
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
        "memory.query_graph",
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


@mcp.tool(name="memory.archive")
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
        "memory.archive",
        lambda: tools.archive(
            storage=deps["storage"],
            max_age_days=max_age_days,
        ),
        ctx,
        f"max_age={max_age_days}d",
        lambda r, m: f"archived={r['nodes_archived']}",
    )


@mcp.tool(name="memory.restore")
async def memory_restore(
    archive_data: dict,
    ctx: Context,
) -> str:
    """Restore previously archived nodes and edges into the graph.

    Args:
        archive_data: The archive dict (as returned by memory.archive).
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "memory.restore",
        lambda: tools.restore(
            archive_data=archive_data,
            storage=deps["storage"],
        ),
        ctx,
        f"nodes={len(archive_data.get('nodes', []))}",
        lambda r, m: f"restored={r['nodes_restored']}",
    )


# --- Timeline tools ---


@mcp.tool(name="memory.create_timeline")
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
        "memory.create_timeline",
        lambda: tools.create_timeline(
            name=name,
            storage=deps["storage"],
            description=description,
        ),
        ctx,
        f"name={name}",
        lambda r, m: f"id={r['timeline_id']}",
    )


@mcp.tool(name="memory.add_timepoint")
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
    from datetime import datetime as dt, timezone
    deps = ctx.lifespan_context
    parsed_start = dt.fromisoformat(start).replace(tzinfo=timezone.utc) if start else None
    parsed_end = dt.fromisoformat(end).replace(tzinfo=timezone.utc) if end else None
    return await _run_with_timeout(
        "memory.add_timepoint",
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


@mcp.tool(name="memory.query_timeline")
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
    from datetime import datetime as dt, timezone
    deps = ctx.lifespan_context
    parsed_target = dt.fromisoformat(target).replace(tzinfo=timezone.utc) if target else None
    parsed_start = dt.fromisoformat(range_start).replace(tzinfo=timezone.utc) if range_start else None
    parsed_end = dt.fromisoformat(range_end).replace(tzinfo=timezone.utc) if range_end else None
    return await _run_with_timeout(
        "memory.query_timeline",
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


@mcp.tool(name="memory.create_timelink")
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
        "memory.create_timelink",
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


@mcp.tool(name="memory.create_metacontext")
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
        "memory.create_metacontext",
        lambda: tools.create_metacontext(
            content=content,
            storage=deps["storage"],
            description=description,
        ),
        ctx,
        f"content={content[:50]}",
        lambda r, m: f"id={r['metacontext_id']}",
    )


@mcp.tool(name="memory.get_metacontexts")
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
        "memory.get_metacontexts",
        lambda: tools.get_metacontexts_for_node(
            node_id=node_id,
            storage=deps["storage"],
        ),
        ctx,
        f"node={node_id}",
        lambda r, m: f"metacontexts={m.nodes_returned}",
    )


if __name__ == "__main__":
    mcp.run()
