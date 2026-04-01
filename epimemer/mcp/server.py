"""FastMCP server for the Epimemer memory system.

Exposes memory tools (ingest, search, link, update, reflect,
query_graph, archive, restore) via the Model Context Protocol.
"""

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastmcp import Context, FastMCP

from epimemer.logging.structured import ToolInvocationLog, log_tool_call, setup_logging
from epimemer.mcp import tools
from epimemer.mcp.config import (
    ServerConfig,
    create_decomposition_provider,
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
    decomposition_provider = create_decomposition_provider(config)

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
        )
        viz_server = uvicorn.Server(viz_config)
        asyncio.create_task(viz_server.serve())
        logging.getLogger(__name__).info(
            "Visualization server started at http://%s:%d",
            config.viz_host,
            config.viz_port,
        )

    try:
        yield {
            "storage": storage,
            "embedding_provider": embedding_provider,
            "decomposition_provider": decomposition_provider,
            "config": config,
            "event_bus": event_bus,
        }
    finally:
        if viz_server is not None:
            viz_server.should_exit = True
        if hasattr(storage, "close"):
            await storage.close()


mcp = FastMCP(
    "epimemer",
    instructions="Epimemer is a layered epistemic memory system. Use memory.ingest to store text, "
    "memory.search to find relevant knowledge, memory.reflect to consolidate the graph, "
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


# --- Tools ---


@mcp.tool(name="memory.ingest")
async def memory_ingest(
    content: str,
    ctx: Context,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
    metacontext_id: str | None = None,
) -> str:
    """Ingest text into the epistemic memory graph.

    Segments the text, extracts topics/facts/inferences via LLM,
    creates typed edges, and stores everything with embeddings.

    Args:
        content: The text to ingest.
        metadata: Optional metadata to attach to the document.
        segmentation_strategy: "paragraph" or "semantic". Uses server default if omitted.
        metacontext_id: Optional metacontext ID — all extracted nodes will inherit this metacontext.
    """
    deps = ctx.lifespan_context
    start = time.monotonic()
    try:
        result, meta = await tools.ingest(
            content=content,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            decomposition_provider=deps["decomposition_provider"],
            config=deps["config"],
            metadata=metadata,
            segmentation_strategy=segmentation_strategy,
            metacontext_id=metacontext_id,
            event_bus=deps.get("event_bus"),
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.ingest", f"content_length={len(content)}", f"segments={result['segments_created']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.search(
            query=query,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            k=k,
            node_types=node_types,
            graph_hops=graph_hops,
            metacontext_id=metacontext_id,
            event_bus=deps.get("event_bus"),
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.search", f"query={query[:50]}", f"nodes={meta.nodes_returned}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.link(
            src_id=src_id,
            dst_id=dst_id,
            edge_type=edge_type,
            storage=deps["storage"],
            weight=weight,
            metadata=metadata,
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.link", f"{src_id}->{dst_id}:{edge_type}", f"edge={result['edge_id']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.update(
            node_id=node_id,
            new_content=new_content,
            storage=deps["storage"],
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.update", f"node={node_id}", f"new={result['new_node_id']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


@mcp.tool(name="memory.reflect")
async def memory_reflect(
    ctx: Context,
    similarity_threshold: float = 0.85,
    decay_rate: float = 0.05,
    auto_merge: bool = True,
) -> str:
    """Run the reflection pipeline on the memory graph.

    Performs topic consolidation (merge similar topics), value decay
    (reduce relevance of stale nodes), and contradiction detection.

    Args:
        similarity_threshold: Cosine similarity threshold for topic merging.
        decay_rate: Multiplicative decay factor for relevance.
        auto_merge: Whether to automatically merge similar topics.
    """
    deps = ctx.lifespan_context
    start = time.monotonic()
    try:
        result, meta = await tools.reflect(
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            decomposition_provider=deps["decomposition_provider"],
            similarity_threshold=similarity_threshold,
            decay_rate=decay_rate,
            auto_merge=auto_merge,
            event_bus=deps.get("event_bus"),
        )
        latency = (time.monotonic() - start) * 1000
        _log(
            "memory.reflect",
            f"threshold={similarity_threshold}",
            f"merged={result['topics_merged']} decayed={result['nodes_decayed']}",
            meta,
        )
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.query_graph(
            node_id=node_id,
            storage=deps["storage"],
            hops=hops,
            edge_types=edge_types,
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.query_graph", f"node={node_id} hops={hops}", f"nodes={meta.nodes_returned}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.archive(
            storage=deps["storage"],
            max_age_days=max_age_days,
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.archive", f"max_age={max_age_days}d", f"archived={result['nodes_archived']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.restore(
            archive_data=archive_data,
            storage=deps["storage"],
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.restore", f"nodes={len(archive_data.get('nodes', []))}", f"restored={result['nodes_restored']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.create_timeline(
            name=name,
            storage=deps["storage"],
            description=description,
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.create_timeline", f"name={name}", f"id={result['timeline_id']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start_time = time.monotonic()
    try:
        # Parse ISO strings to datetimes
        parsed_start = dt.fromisoformat(start).replace(tzinfo=timezone.utc) if start else None
        parsed_end = dt.fromisoformat(end).replace(tzinfo=timezone.utc) if end else None

        result, meta = await tools.add_timeline_timepoint(
            timeline_id=timeline_id,
            storage=deps["storage"],
            start=parsed_start,
            end=parsed_end,
            label=label,
        )
        latency = (time.monotonic() - start_time) * 1000
        _log("memory.add_timepoint", f"timeline={timeline_id}", f"tp={result['timepoint_id']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start_time = time.monotonic()
    try:
        parsed_target = dt.fromisoformat(target).replace(tzinfo=timezone.utc) if target else None
        parsed_start = dt.fromisoformat(range_start).replace(tzinfo=timezone.utc) if range_start else None
        parsed_end = dt.fromisoformat(range_end).replace(tzinfo=timezone.utc) if range_end else None

        result, meta = await tools.query_timeline(
            timeline_id=timeline_id,
            storage=deps["storage"],
            target=parsed_target,
            range_start=parsed_start,
            range_end=parsed_end,
            k=k,
        )
        latency = (time.monotonic() - start_time) * 1000
        _log("memory.query_timeline", f"timeline={timeline_id}", f"timepoints={meta.nodes_returned}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.create_timelink(
            node_id=node_id,
            timeline_id=timeline_id,
            timepoint_id=timepoint_id,
            storage=deps["storage"],
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.create_timelink", f"{node_id}->{timeline_id}:{timepoint_id}", f"edge={result['edge_id']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.create_metacontext(
            content=content,
            storage=deps["storage"],
            description=description,
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.create_metacontext", f"content={content[:50]}", f"id={result['metacontext_id']}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


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
    start = time.monotonic()
    try:
        result, meta = await tools.get_metacontexts_for_node(
            node_id=node_id,
            storage=deps["storage"],
        )
        latency = (time.monotonic() - start) * 1000
        _log("memory.get_metacontexts", f"node={node_id}", f"metacontexts={meta.nodes_returned}", meta)
        return _build_response(result, meta, latency)
    except Exception as e:
        return _error_response(str(e))


if __name__ == "__main__":
    mcp.run()
