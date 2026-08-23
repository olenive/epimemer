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
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation

from epimemer.core.types import JudgeRef
from epimemer.logging.structured import ToolInvocationLog, log_tool_call, setup_logging
from epimemer.pipelines.reflection.review import SIMILARITY_NOMINATION_THRESHOLD
from epimemer.mcp import tools
from epimemer.mcp.config import (
    create_embedding_provider,
    create_storage,
    load_config,
)
from epimemer.mcp.retrieval_records import (
    RetrievalRecord,
    append_record,
    new_record_log,
    next_record_id,
    records_of,
    structural_only,
)
from epimemer.mcp.types import ResponseMeta, ToolResponse
from epimemer.visualization.events import RetrievalRecorded


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

    # Ids the user admitted before this process started (REVIEW_MODE.md §10.3).
    # Seeded here rather than read at every check so that one channel writes the
    # approved list and everything else reads it — and because on an embedded
    # backend this is the *only* channel that reaches the running server, the
    # `epimemer agents confirm` CLI being a separate store (ISSUES.md #16).
    await tools.approve_agent_ids(storage, config.approved_agents)

    embedding_provider = create_embedding_provider(config)

    # Optional: publish visualization events to the standalone hub. This process
    # never binds the viz port itself — it dials out to the hub (auto-spawning one
    # if none is running), so stale MCP orphans become dead sessions rather than a
    # stray server answering on the port with the wrong graph.
    stop_viz_client = None
    event_bus = None
    viz_session = None
    viz_hub_url = None
    # Written at the tool choke point, read by the hub's `retrievals` RPC. It
    # exists whether or not visualization is on: the record is what the agent
    # was handed, and that is worth keeping even with nobody watching.
    retrievals = new_record_log()
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
            records=lambda: [
                json.loads(record.model_dump_json())
                for record in records_of(retrievals)
            ],
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
            "retrievals": retrievals,
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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


async def _record_response(
    deps: dict, tool_name: str, input_summary: str, response_text: str, meta: ResponseMeta
) -> None:
    """Keep what this call handed the agent (`RETRIEVAL_PROVENANCE.md` §3).

    One insertion here covers every tool, including ones not written yet — the
    alternative was six wrappers that drift, and the census of which six was
    wrong twice. What it cannot do by construction is know the *ids*: those are
    declared on `meta` by each tool, and `retrieved is None` means a tool never
    declared, which the record carries as a visible gap rather than an
    indistinguishable empty list (§2.1).

    Failures write nothing: there is no response to record, and the selector
    lists what the agent was handed.
    """
    record = RetrievalRecord(
        record_id=next_record_id(),
        tool=tool_name,
        query=input_summary,
        graph=deps["storage"].current_database,
        retrieved=meta.retrieved,
        response_text=response_text,
    )
    append_record(deps["retrievals"], record)

    bus = deps["event_bus"]
    if bus is None:
        return
    # The guard is the **bind**, not the process (§3.2). A hub bound to
    # loopback is reachable only from this machine, so the mirror carries the
    # whole record and survives session death; a hub anyone can reach gets
    # structural metadata only, and the payload stays here behind the
    # `retrievals` RPC for as long as this process lives.
    exposed = record if deps["config"].viz_host in _LOOPBACK_HOSTS else structural_only(record)
    await bus.publish(RetrievalRecorded(
        graph=record.graph,
        record=json.loads(exposed.model_dump_json()),
    ))


# The tools that move the active graph, and so take the guard's mover turn
# rather than a user's. `use_graph` is the whole list: `delete_graph` removes a
# graph without pointing anything at it, and every other tool works against
# wherever the server already is. Naming them here rather than at each call site
# keeps the list readable as a list — the invariant is *these and no others*.
MOVES_THE_GRAPH = frozenset({"epimemer.use_graph"})


def _graph_turn(deps: dict, tool_name: str, waits_for_user: bool):
    """The guard turn this tool call takes, if any (#16).

    **A call that waits on a person takes none.** `claim_agent` blocks until
    somebody answers an elicitation, and a user's turn held across that would
    stall every snapshot behind a prompt nobody has read yet — turning *the
    dashboard is a few seconds stale* into *the dashboard is down*. The residue
    is stated rather than hidden: a snapshot borrow landing mid-elicitation can
    still redirect that one call's write, which is a graph's `agent` table, and
    the trade is deliberate.
    """
    if waits_for_user:
        return nullcontext()
    guard = deps["storage"].graph_guard
    return guard.moving() if tool_name in MOVES_THE_GRAPH else guard.using()


async def _run_with_timeout(
    tool_name: str,
    coro: Callable[[], Awaitable[tuple[dict, ResponseMeta]]],
    ctx: Context,
    input_summary: str,
    output_summary_fn: Callable[[dict, ResponseMeta], str],
    waits_for_user: bool = False,
) -> str:
    """Run a tool coroutine with timeout, logging, and error handling.

    `waits_for_user` drops the timeout, and only a call that puts a question to
    a person through `ctx.elicit` may set it. The budget bounds work the server
    is doing; a human reading a prompt is not that, and killing the request at
    30s would turn *the user was still reading* into *the client cannot elicit*
    — which is the difference between a claim refused and an identity admitted.

    **This is also where a call takes its turn over the active graph.** One tool
    call is one logical operation, and the invariant #16 exists for is that the
    graph does not move underneath one — so the turn has to be taken here, at
    the boundary, rather than inside the storage calls that make up the work.
    """
    deps = ctx.lifespan_context
    timeout = None if waits_for_user else deps["config"].tool_timeout_seconds
    start = time.monotonic()
    try:
        async with _graph_turn(deps, tool_name, waits_for_user):
            result, meta = await (
                coro() if timeout is None else asyncio.wait_for(coro(), timeout=timeout)
            )
        latency = (time.monotonic() - start) * 1000
        _log(tool_name, input_summary, output_summary_fn(result, meta), meta)
        response_text = _build_response(result, meta, latency)
        await _record_response(deps, tool_name, input_summary, response_text, meta)
        return response_text
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
    published_at: dict | None = None,
    metadata: dict | None = None,
    segmentation_strategy: str | None = None,
    expected_graph: str | None = None,
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
        published_at: When the document was published, as an imprecise instant:
            {"instant_kind": "precise", "at": "1970-06-01"} for a date, or
            {"instant_kind": "named", "label": "spring 1970"} for a phrase the
            text gives without a date. Omit it when the document carries no
            publication date — it is deliberately **not** defaulted to now, since
            an undated document must not end up claiming its facts were witnessed
            on the day you happened to ingest it. Supply only what the document
            says, never a date you know from elsewhere.
        metadata: Optional metadata to attach to the document.
        segmentation_strategy: "paragraph" or "semantic". Uses server default if omitted.
        expected_graph: The graph you believe you are working in. Optional, and
            worth passing whenever you know it: the write is **refused** rather
            than misfiled if the server is on a different one. The active graph
            is not remembered across a client reconnect, so a session that
            called use_graph earlier can come back somewhere else — and an
            ingest into the wrong graph succeeds in every other respect. The
            response names `active_graph` either way; thread it into
            store_decomposition.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
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
            published_at=published_at,
            metadata=metadata,
            segmentation_strategy=segmentation_strategy,
            event_bus=deps.get("event_bus"),
            judge=judge,
            expected_graph=expected_graph,
        ),
        ctx,
        f"content_length={len(content)}",
        lambda r, m: f"graph={r['active_graph']} segments={len(r['segments'])}",
    )


@mcp.tool(name="store_decomposition")
async def memory_store_decomposition(
    document_id: str,
    segments: list[dict],
    ctx: Context,
    metacontext_id: str | None = None,
    tags: list[str] | None = None,
    timeline_id: str | None = None,
    propose_timepoints: bool = True,
    expected_graph: str | None = None,
) -> str:
    """Store your decomposition of segments into topics, facts, and inferences.

    This is step 2 of the two-step ingest flow. Call this after segment
    with your extracted nodes.

    The response includes stores_since_reflect and reflect_threshold. When
    reflect_suggested is true, suggest running reflect to the user. The count
    belongs to the active graph, so it accumulates across reconnects and follows
    a use_graph switch.

    It also includes **historical_twins**: facts you just stored that are
    word-for-word a claim the graph previously retired as `historical`. That is
    a recurrence — the same claim true again — and the fix is `restore` with the
    named `historical_id`, not two nodes saying the same thing. It only catches
    verbatim matches; run check_conflicts for the ones phrased differently,
    which is most of them.

    Args:
        document_id: The document ID returned by segment.
        segments: List of decomposed segments. Each entry:
            segment_id: str — from segment result
            topics/facts/inferences: each item is either a content string, or
              an object {"content": str, "tags": ["billing", ...],
              "importance": 0.8, "confidence": 0.9, "confidence_basis": "...",
              "claim_kind": "state"} to attach per-node tags, either value
              prior, and (on facts) the condition-or-occurrence judgment.
            importance: 0.0–1.0, default 0.5. Set it only when you already know
              a node is unusually consequential or unusually disposable —
              importance is properly judged at reflect time, and `reinforce` is
              how it rises later.
            confidence: 0.0–1.0 — how well the record would back this claim up
              if it were challenged. A property of the evidence, not of how far
              you agree with the conclusion, and not of how much it matters.
              **Omit it** unless one of these fits; omitting means "stated
              plainly, no specific reason to doubt or specially trust it", and
              is recorded as unrated rather than as a middling score.
                0.3 — the source hedges ("reportedly", "one account says"), is
                     partisan on this point, or the claim is your reading of
                     the text rather than something it states
                0.7 — stated as established, by a source in a position to know
                0.9 — a primary or authoritative source *for this claim*: the
                     person about their own preference, the spec about its own
                     behaviour, the original announcement
              Rate per node, never per document — one message can carry a 0.9
              preference and a 0.3 guess from the same person. Inside a
              metacontext the frame is the record, so a fictional fact can
              honestly be 0.9. Never lower it for contradiction or for age:
              record_contradiction and created_at carry those already, and
              unlike a frozen prior they stay current.
            confidence_basis: one line saying why. Asked for whenever you
              supply a confidence other than the 0.5 default — a high prior
              nobody can review later is worth little — but never required.
            claim_kind: "state" or "event" — **facts only**, and supplying it on
              a topic or inference is an error. Ask what kind of thing is being
              claimed, not how strongly:
                "state" — a condition that holds over a period, and can hold
                     again later. "Labour is in government", "the city is called
                     Leningrad", "the service runs on port 8080"
                "event" — something that happened on an occasion. "Labour won
                     the election", "the city was renamed", "the deploy failed
                     at 14:02"
              This is the judgment fact deduplication is gated on, and it is the
              one nothing downstream can make: two documents years apart yield
              near-identical sentences, and merging them is right for a state
              (one condition, two periods) and fabricates history for an event
              (two elections become one twenty-seven-year victory). You have the
              document in front of you — the tense, the sentences either side,
              whether "the election" is a particular one — and a later merge
              sees two stripped sentences with none of it.
              **Omit it when you genuinely cannot tell.** Omitted means unjudged
              and the fact simply never merges, which costs a tidier graph;
              guessing costs corroboration that was never earned.
            validity: when *this document* says the claim was true. A list,
              because one source can assert several separate periods ("Labour
              governed 1997–2010, and again from 2024"). It is recorded against
              this document, so two sources may disagree without either being
              overwritten. **Omit it entirely unless the text tells you** — that
              is the common case and costs nothing.
              Each entry: {"start": <instant>, "end": <instant>, "basis":
              "stated" | "inferred", "witnessed_at": <instant>, "timeline_id":
              "..."}. An <instant> is one of:
                {"instant_kind": "precise", "at": "1924-01-31"} — a date given
                {"instant_kind": "named", "label": "during the Renaissance"} —
                     a period the text names but does not date. Store the words;
                     do not convert them to a date yourself
                {"instant_kind": "unknown"} — there is a boundary and the text
                     does not give it. The default for a missing endpoint
                {"instant_kind": "unbounded"} — there is no boundary at all
                     ("water is H₂O"). Never use it for "not stated"
              `unknown` and `unbounded` are the distinction the whole field
              exists for: *"the city is named Leningrad"* has an unknown start,
              and guessing either way invents information.
              basis: "stated" if the dates are in the text; "inferred" if you
              read them off tense or context ("the city *is* called Leningrad"
              in a 1970 document leaves the end open). Both are honest; **a date
              you know from world knowledge and the document does not give is
              neither, and must not be supplied at all** — an invented interval
              is indistinguishable from a documented one once stored.
              witnessed_at: a moment the document asserts the claim held at
              ("as of March 1990…"). Worth giving when the period itself is
              unknown: it is the only thing that lets two undated facts be shown
              to have held at the same time.
              timeline_id: omit for real-world dates. Give it for in-universe
              time (a novel's chronology), so fictional and real dates are never
              compared as though they shared a clock.
        metacontext_id: Optional metacontext ID — all nodes will inherit this.
        tags: Optional document-level tag names applied to every node. Each tag
            becomes (or reuses) a Topic linked by a tagged_with edge. Every node
            also gets a sourced_from edge to the document.
        timeline_id: Optional timeline to propose timepoints onto — use it when
            the document belongs to a timeline you have already created (a
            novel's chronology, a project history). It must exist. Omitted,
            proposals go to the shared "Extracted" timeline.
        expected_graph: The graph you believe you are working in — pass
            `active_graph` from the segment response. Refused rather than
            misfiled if the server has moved. Checked here as well as in
            segment, because a document segmented in the wrong graph *has* its
            segments there: the two steps agreeing says nothing about either
            being right.
        propose_timepoints: Dates stated in node content ("on 12 March 1997",
            "the 1990s") become timepoints linked by TIMELINK, so content-time
            mode has something to show. Vague expressions stay undated rather
            than being guessed at. Set false to skip.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused

    async def _do() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.store_decomposition(
            document_id=document_id,
            segments=segments,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            metacontext_id=metacontext_id,
            tags=tags,
            timeline_id=timeline_id,
            propose_timepoints=propose_timepoints,
            event_bus=deps.get("event_bus"),
            judge=judge,
            expected_graph=expected_graph,
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
        lambda r, m: f"graph={r['active_graph']} nodes={m.nodes_returned} edges={r['edges_created']} timepoints={r['timepoints_proposed']} reflect={r['stores_since_reflect']}/{r['reflect_threshold']}",
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
    terms: list[str] | None = None,
    include_historical: bool = True,
    include_corrected: bool = False,
    valid_as_of: str | None = None,
    timeline_id: str | None = None,
    include_corroboration: bool = False,
) -> str:
    """Search the epistemic memory graph.

    Hybrid retrieval: embedding similarity and keyword matching run
    independently and are fused by rank, then graph expansion pulls in what the
    winners connect to. Results always include metacontext labels and computed
    review labels (superseded_candidate / evidence_stale / evidence_merged /
    contested) so you can see when a node may be outdated, have stale evidence,
    rest on a premise that absorbed another claim, or be contested before relying
    on it.

    **Pass identifiers, names and exact phrases you care about as `terms`.** A
    ticket id, an error code, a person's name, a filename. Embeddings shred
    those — `JIRA-4417` becomes word pieces pooled with the rest of the
    sentence, so every other ticket id scores about as well — and keyword
    matching supplies the term rarity similarity has no notion of. A declared
    term's best hit is kept even if rank fusion would otherwise have cut it.

    Each returned node carries `provenance`: `lexical` (a term matched its
    content), `segment` (a term matched the passage it came from), `vector`
    (similarity), or `expanded` (reached by an edge from one of those). The
    response also carries `segments` — the passages that matched, which answer
    *where did I read that?* rather than *what do I believe?*

    **Read each result's `status` before leaning on it.** `active` is a current
    claim. `historical` is one the world moved past — still right of its period,
    which is why it is returned, and wrong to quote as current. `corrected` is
    one concluded false, and only appears if you asked for it.

    A claim's retired versions do not each take a slot. When a retired node and
    the claim that replaced it both match, the replacement is the result and the
    retired one comes back as `earlier_versions` on it — id, a content preview
    and its status — so one answer arrives with its history attached instead of
    four near-identical rows.

    **`validity` says when a claim was true, per source.** Each entry is one
    source and the periods it asserts; two sources may disagree and neither is
    overwritten. Most nodes have none, and its absence means nobody dated the
    claim — not that the claim is undated in the world.

    **`valid_as_of` asks what was true then, and answers with groups.** Every
    result gets `valid_at`: `valid` (some source asserts it held then) or
    `unknown` (nobody says), with the same split repeated as id lists at the top
    level. Nothing is excluded, and that is deliberate — an interval says what a
    source asserts and asserts nothing about the outside, so a moment nobody
    dated is unknown rather than false. A filter would turn a missing date into a
    confident "no", which is the one answer this memory must not invent.

    **`include_corroboration` asks how many independent sources back a claim.**
    Off by default because it is expensive, not because it is unimportant. Turn
    it on when independence is the question — *is this one report repeated, or
    several outlets agreeing?* — and expect the search to take noticeably
    longer.

    Read the number for what it counts: **distinct publishers**, not strength of
    evidence. Three hedged reports from three outlets score 3, exactly as three
    confident ones would, so it does not interact with `confidence` and neither
    substitutes for the other. Documents naming no publisher stand as their own
    source, and `unattributed_documents` says how many did — a graph ingested
    without attribution scores lower for that reason alone. Every source names
    the nodes and documents behind it, so an implausible number can be checked
    rather than taken on trust.

    **`adjacent_periods` is not a rejection list — read it.** A claim whose
    stated periods provably fall clear of this one's is about a *different*
    stretch of time, so it is not a second witness and does not count; it comes
    back here instead, with its publisher and its own periods. Both claims are
    true and both remain in the graph. Where your search returned one of the
    pair and not the other, this is the only place the other appears — so treat
    an entry as *"the graph also knows what was true next door"*, and follow it
    if the period matters to the question you were asked.

    For provenance/topic listings (which nodes came from X / are about Y), use
    find_nodes, not search.

    Args:
        query: Natural language search query.
        k: Maximum number of results per retrieval arm.
        node_types: Filter to specific types: "topic", "fact", "inference".
        graph_hops: Number of graph traversal hops from the fused results.
        metacontext_id: Optional — frame-scope results to this metacontext plus
            untagged base-reality nodes (other frames are excluded).
        cross_frame: Set true to ignore frame scoping and search across all
            metacontexts (opt-in; otherwise frames don't bleed together).
        terms: Exact strings that matter — identifiers, names, phrases. Matched
            whole and ORed; each matches only documents containing all of its
            words. Omit and the keyword arm falls back to the query's own words,
            which still finds rare ones but guarantees nothing.
        include_historical: Claims the world moved past. On by default —
            knowledge that is not current is still knowledge. Turn it off when
            you want only what holds now.
        include_corrected: Claims concluded wrong. Off by default. Turn it on
            for "what did we believe about X that turned out wrong?", and expect
            to see things the graph has already ruled against.
        valid_as_of: ISO datetime to judge validity at — *when the claim was
            true*, not when the graph learned it. For "is this current" inside a
            fictional or otherwise separate timeline, pass that timeline's own
            reference time: its present is a fact about that world, not the wall
            clock.
        timeline_id: The clock `valid_as_of` is read against. Omit for the
            wall-clock timeline, which is what real-world facts use. Periods
            recorded on another clock are not comparable and count as unknown.
        include_corroboration: How many independent sources back each result.
            Off by default on cost — it is several times the price of every
            other annotation and rises with how much the graph has been
            reflected over. Turn it on when you need to weigh independence.
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
            terms=terms,
            include_historical=include_historical,
            include_corrected=include_corrected,
            valid_as_of=_parse_utc(valid_as_of) if valid_as_of else None,
            timeline_id=timeline_id,
            include_corroboration=include_corroboration,
            record_retrieval=deps["config"].record_retrieval,
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
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
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
            judge=judge,
        ),
        ctx,
        f"{src_id}->{dst_id}:{relation or edge_type}",
        lambda r, m: f"edge={r['edge_id']}",
    )


@mcp.tool(name="update")
async def memory_update(
    node_id: str,
    new_content: str,
    because: str,
    ctx: Context,
) -> str:
    """Update a node by creating a new version (immutable history).

    The old node is retired and a new node is created, joined by a lineage edge
    that names which of the two retirements happened: `superseded_by` for a
    correction, `temporally_followed_by` for a world-change. The second states
    temporal order rather than replacement, so a claim that becomes true again
    later does not contradict it.

    Args:
        node_id: ID of the node to update.
        new_content: The updated content.
        because: Why the old version is being retired — one of:
            "it_was_wrong" — the old content was mistaken, and should not have
                been believed. The node is kept as an audit trail.
            "the_world_changed" — the old content was correct and **remains
                correct of its period**; it is simply no longer current. A city
                renamed, a government replaced, a price that has since moved.
            There is no default, because the two are opposite claims about the
            old node and picking one silently mislabels the other. A graph that
            files world-changes as errors forgets its own history.

            **If you cannot tell which happened, do not pick one.** Two undated
            claims and no knowledge of which came first is not enough to judge,
            and a guessed `because` reads afterwards as a judgment someone made.
            Record a `record_contradiction` instead and leave the pair contested
            for whoever can resolve it.

    Note on world-changes: the replacement's sources are its own, and this tool
    writes content *you* authored, so a world-change resolved here can leave the
    new node with no source at all. Prefer ingesting the document that reports
    the change and resolving with `supersede_by` against the fact it produced.
    Reach for `update` with "the_world_changed" when you can genuinely attribute
    the new content; if you cannot say where it came from, that is worth
    noticing rather than working around.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.update",
        lambda: tools.update(
            node_id=node_id,
            new_content=new_content,
            because=because,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            judge=judge,
        ),
        ctx,
        f"node={node_id}",
        lambda r, m: f"new={r['new_node_id']}",
    )


@mcp.tool(name="supersede_by")
async def memory_supersede_by(
    old_id: str,
    existing_id: str,
    because: str,
    ctx: Context,
) -> str:
    """Supersede a node by an already-existing node (resolve outdated/contradiction).

    Marks old_id retired and joins it to existing_id with the lineage edge that
    matches `because` — `superseded_by` for a correction,
    `temporally_followed_by` for a world-change. Flags inferences that depended
    on old_id as evidence_stale, and clears any supersession candidacy on it.
    The existing node is unchanged. Use when the current truth is already in the
    graph; use `update` when you have new content.

    Args:
        old_id: The node being retired.
        existing_id: The existing node that supersedes it.
        because: Why old_id is being retired — "it_was_wrong" (it was mistaken)
            or "the_world_changed" (it was correct and remains correct of its
            period, just not current). No default, and no guessing: if you
            cannot tell the two apart, `record_contradiction` and leave the pair
            contested rather than inventing a reason. See `update`.

    On "the_world_changed", the retired node keeps its own sources, tags and
    relationships — it is still true of its period, and what its sources said
    about it stays said about it. `existing_id` is untouched: its provenance is
    its own.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.supersede_by",
        lambda: tools.supersede_by(
            old_id=old_id,
            existing_id=existing_id,
            because=because,
            storage=deps["storage"],
            judge=judge,
        ),
        ctx,
        f"old={old_id} by={existing_id}",
        lambda r, m: f"superseded={r['superseded_id']}",
    )


@mcp.tool(name="judge_importance")
async def memory_judge_importance(
    node_id: str,
    direction: str,
    reason: str,
    ctx: Context,
    related_id: str | None = None,
) -> str:
    """Record that a node matters more — or less — than its importance says.

    Judge **up** when you learn something raising an existing node's standing:
    new evidence supporting it, a decision that turned out to hinge on it, a
    fact that keeps proving load-bearing.

    Judge **down** when a node's importance has expired rather than its truth —
    an error record that mattered until the bug was fixed, a decision that has
    since been superseded by circumstances. Importance is what protects a node
    from the archival sweep, so a stale high judgment keeps junk alive
    indefinitely; judging it down is how you hand it back to review without
    asserting it should be archived outright.

    Retrieval already records that a node was used, so there is no need to
    judge a node up merely because you read it.

    Each call moves importance asymptotically toward its bound — repeated calls
    approach 1.0 or 0.0 without reaching either — and appends the reason to the
    node's judgment trail. There is no way to set the number directly, because
    an unattributable judgment cannot be reviewed later, and because a raw value
    would overwrite every judgment that came before it.

    Args:
        node_id: The node being judged.
        direction: "up" or "down".
        reason: Why — this is read by whoever reviews the judgment.
        related_id: Optional — the node whose arrival triggered the
            reassessment.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.judge_importance",
        lambda: tools.judge_importance(
            node_id=node_id,
            direction=direction,
            reason=reason,
            storage=deps["storage"],
            related_id=related_id,
            importance_step=deps["config"].importance_step,
            judge=judge,
        ),
        ctx,
        f"node={node_id} direction={direction} related={related_id}",
        lambda r, m: f"importance={r['importance']:.3f} judgments={r['judgments']}",
    )


@mcp.tool(name="check_conflicts")
async def memory_check_conflicts(
    fact_ids: list[str],
    ctx: Context,
    threshold: float = SIMILARITY_NOMINATION_THRESHOLD,
    k: int = 5,
) -> str:
    """Find existing facts that may conflict with the given facts (you then judge).

    Recall stage of the review loop: for each fact, returns similar facts above
    `threshold` with their similarity score, **status**, metacontext labels, and
    a same_frame flag. Similarity only nominates — classify each candidate
    yourself and record the verdict. Run this on freshly-ingested fact ids to
    catch outdated or conflicting knowledge.

    **Read the candidate's `status` first — it decides which verdicts are even
    available.**

      `active`      — the graph currently asserts it. Any verdict below applies.
      `historical`  — it was retired because the world moved on, and it may be
                      true again. This is the one that makes `recurs` possible.

    The verdicts:

      redundant   — an *active* candidate says the same thing. Keep one; no
                    tool call is needed for the duplicate.
      supersedes  — your new fact replaces the candidate. Call supersede_by,
                    and say which happened: it_was_wrong, or the_world_changed.
      recurs      — a *historical* candidate says what your new fact says: the
                    same claim, true again. Call restore with node_ids=[that
                    candidate] and sourced_from=<your document id>, rather than
                    letting a second node be stored alongside it. Labour out of
                    government in 2010 and back in 2024 is one claim recurring.
      contradicts — both claim to hold now and they cannot both. Call
                    record_contradiction.
      cross-frame — same words, different frames (fiction vs. reality). Call
                    record_variant.
      compatible  — related but not in tension. Nothing to record.

    `recurs` and `supersedes` are the pair worth getting right, and the question
    that separates them is which node the new source agrees with. If the new
    fact says what the historical one said, it recurs. If it says something
    *different* that follows it, it supersedes.

    Args:
        fact_ids: Fact node ids to check (e.g. facts just stored).
        threshold: Minimum cosine similarity for a candidate (default 0.80 —
            the one nomination bar; `merge_facts` refuses below the same
            number, so anything nominated here is mergeable).
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
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.record_contradiction",
        lambda: tools.record_contradiction(
            a_id=a_id,
            b_id=b_id,
            storage=deps["storage"],
            judge=judge,
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
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.record_variant",
        lambda: tools.record_variant(
            a_id=a_id,
            b_id=b_id,
            storage=deps["storage"],
            judge=judge,
        ),
        ctx,
        f"{a_id}<->{b_id}",
        lambda r, m: f"created={r['created']}",
    )


@mcp.tool(name="merge_facts")
async def memory_merge_facts(
    source_ids: list[str],
    content: str,
    ctx: Context,
) -> str:
    """Collapse facts that restate one claim into a single node.

    The action for the `redundant` verdict: two documents assert the same thing
    in different words, and the graph should hold one fact with both sources
    rather than two facts each with one. The survivor keeps a sourced_from edge
    per contributing document, with each document's own validity periods, so
    provenance becomes plural rather than being overwritten — which is what
    makes corroboration mean anything.

    **Merge only what you would defend as the same proposition.** A wrong merge
    is worse than a missed one and not symmetrically so: two distinct claims
    fused into one node with two independent sources read as *better supported*
    than either was, so the mistake does not lose information, it manufactures
    agreement. When unsure, use link(type="similarity") and keep both — nothing
    downstream is harmed by two nodes saying one thing.

    Merges are refused, with a reason, when: any fact is not active (a
    recurring historical twin is `restore`, not a merge); the facts do not stand
    in exactly the same frames (that is `record_variant`); any of them is an
    **event** rather than a state; any of them was ingested without a
    `claim_kind`; or a pair falls below the similarity nomination bar. A refusal
    comes back as `merged: false` with `refused` — read it, it says which.

    The event rule is the one worth understanding. "Labour won the election"
    from a 1997 document and from a 2024 one are two victories that look like
    one sentence; merging unions their periods into a single win spanning both.
    Facts stored as "state" — conditions that hold over a period — are the ones
    a merge is safe for, because their periods genuinely do union.

    Args:
        source_ids: Two or more fact ids to collapse. All are retired as MERGED
            and linked to the survivor by merged_into, so nothing is lost.
        content: The claim as the surviving fact should state it. Write the
            clearest phrasing of the shared claim rather than picking one
            source's wording — this is new text and it is what gets embedded.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.merge_facts",
        lambda: tools.merge_facts(
            source_ids=source_ids,
            content=content,
            storage=deps["storage"],
            embedding_provider=deps["embedding_provider"],
            judge=judge,
        ),
        ctx,
        f"sources={len(source_ids)}",
        lambda r, m: (
            f"merged={r['fact_id']} from={r['sources_retired']}"
            if r["merged"]
            else f"refused: {r['refused']}"
        ),
    )


@mcp.tool(name="reverse_merge")
async def memory_reverse_merge(
    survivor_id: str,
    ctx: Context,
) -> str:
    """Undo a merge: restore the merged facts and remove the survivor.

    Use it when a merge turns out to have collapsed two claims that were not the
    same one. The sources come back active with their own sources and edges, and
    the graph is left as it was before the merge — including the case where two
    of them cited the same document, which the merge had collapsed into one
    edge.

    **This is the only action that destroys a node**, and only this node: a
    merge survivor's wording was written by an agent rather than drawn from a
    document, and every claim it carried goes back to the facts it came from.
    Nothing else here deletes anything — a wrong claim is `update`, a claim the
    world moved past is `update` with because="the_world_changed", and both keep
    the old node as history.

    Refused, with a reason, when the fact was not made by a merge, when the
    record needed to replay it has aged out (permanent), when the survivor has
    since been merged again, or when **anything has been added to it since the
    merge** — a contradiction, a tag, a similarity verdict. In that last case
    the edges would be destroyed along with the node, so deal with them first.

    If you merge, reverse, and merge the same facts repeatedly, the merge will
    refuse and ask you to bring in the user. That is deliberate: it means the
    judgment is contested and another round trip will not settle it.

    Args:
        survivor_id: The id of the fact a merge produced — the one `merge_facts`
            returned as `fact_id`.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.reverse_merge",
        lambda: tools.reverse_merge(
            survivor_id=survivor_id,
            storage=deps["storage"],
            judge=judge,
        ),
        ctx,
        f"survivor={survivor_id}",
        lambda r, m: (
            f"restored={len(r['restored_ids'])} edges={r['edges_restored']}"
            if r["reversed"]
            else f"refused: {r['refused']}"
        ),
    )


@mcp.tool(name="configure_merge")
async def memory_configure_merge(
    ctx: Context,
    undo_depth: int | None = None,
    cycle_limit: int | None = None,
    clear: bool = False,
) -> str:
    """Read or change this graph's merge settings.

    Called with no arguments it reports what is in force. Ask the user before
    changing either — both are policy about how much the graph keeps and how
    hard it argues back, which is not the agent's call to make alone.

    Args:
        undo_depth: How far back along a chain of merges the graph keeps what a
            reversal needs (default 10). **Lowering this cannot be undone**: the
            record it drops exists only at merge time, so a merge made under a
            lower setting is permanently irreversible.
        cycle_limit: How many times one fact may be merged and un-merged before
            the next merge refuses and asks for a human (default 2). Raise it
            when a refusal is wrong and the merge is right.
        clear: Return both to the defaults.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.configure_merge",
        lambda: tools.configure_merge(
            storage=deps["storage"],
            undo_depth=undo_depth,
            cycle_limit=cycle_limit,
            clear=clear,
        ),
        ctx,
        f"undo_depth={undo_depth} cycle_limit={cycle_limit} clear={clear}",
        lambda r, m: (
            f"undo_depth={r['merge_undo_depth']} "
            f"cycle_limit={r['merge_cycle_limit']}"
        ),
    )


@mcp.tool(name="reflect")
async def memory_reflect(
    ctx: Context,
    similarity_threshold: float = 0.85,
    relation_similarity_threshold: float = 0.9,
    max_nominations: int = tools.MAX_NOMINATIONS,
) -> str:
    """Analyse the memory graph and return candidates for you to act on.

    Reads only — nothing here changes the graph. Identifies:
    - Similar topic pairs that could be consolidated under a parent (this also
      covers duplicate source/tag/entity Topics)
    - Topics with high internal variance that could be split
    - Topics with thin descriptions but rich associated material
    - Potential contradictions between facts (same-frame only) — both sides
      active, since that is what makes them rivals
    - recurrences: a live fact saying what a `historical` one said, meaning the
      claim is true again rather than in conflict. Resolve with restore
      (node_ids=[the historical id], sourced_from=<the document>) — not with
      supersede_by, and not by leaving two nodes saying the same thing
    - boundary_proposals: where a succession lets one claim's period close and
      the next one's open — the successor's own start date moved onto the
      predecessor, which no single document could supply. Each shows `current`
      and `proposed` for the period it would change, and the claim and source
      that license it. Accept the ones you agree with via
      apply_reflection(boundaries=[...]); the basis becomes `inferred`, and
      nothing is written until you ask
    - unsound_inferences: an inference whose premises no source puts in the same
      period — *"X was true 1997–2010"* and *"Y was true from 2024"*, combined
      into a conclusion. It reports the pairs and their dates, never a verdict:
      decide whether the inference survives, narrow it to a period, or retire it
      with supersede_by. It stays silent unless both premises carry dates and
      those dates provably fall clear, so it says *no source asserts these were
      ever both true* — not that they never were
    - pending_review: active nodes already flagged for resolution
      (superseded_candidate / evidence_stale / evidence_merged / contested),
      with the related ids to act on via apply_reflection supersessions /
      supersede_by. evidence_merged asks for a re-read rather than a
      resolution: the premise absorbed another claim, so check the inference
      still says what the survivor's wording supports
    - similar_relations: likely-synonymous user relationship labels to consolidate
      via apply_reflection relation_merges
    - truncated: the names of any lists that hit `max_nominations` and were cut
      to their highest-scoring entries. Empty on an ordinary graph. When a list
      is named here, treat it as *this graph is denser than one pass can
      report*: act on what came back and reflect again, rather than reaching for
      a bigger number — the remainder is the weakest end of the ranking

    Review the candidates and call apply_reflection with your decisions.

    For large graphs, consider delegating this to a subagent so analysis
    and decision-making don't consume your main conversation context.

    Args:
        similarity_threshold: Cosine similarity threshold for finding similar pairs.
        relation_similarity_threshold: Similarity bar for proposing relationship-
            label consolidations.
        max_nominations: Most entries returned in any one pair list. The pair
            lists are quadratic in the graph; this bounds the response.
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
            relation_similarity_threshold=relation_similarity_threshold,
            max_nominations=max_nominations,
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
            f"pairs={len(r['similar_pairs'])} "
            f"pending={len(r['pending_review'])} relations={len(r['similar_relations'])}"
            # A cut response should say so where the operator reads, not only
            # where the agent does.
            + (f" truncated={','.join(r['truncated'])}" if r["truncated"] else "")
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
    judgments: list[dict] | None = None,
    relation_merges: list[dict] | None = None,
    boundaries: list[dict] | None = None,
    similarities: list[dict] | None = None,
    merge_similarity_threshold: float = 0.92,
) -> str:
    """Apply your reflection decisions to the memory graph.

    Call this after reviewing reflect results. All arguments optional.

    Args:
        similarities: What you decided about a pair from reflect's
            similar_pairs that you are **not** otherwise acting on — the verdict
            that previously had no writer, which is why the same pairs kept
            coming back. Each: {pair: [a_id, b_id], verdict: "one_claim" |
            "distinct", because: str}.
            Use "one_claim" when the two really do say the same thing and
            something blocked the merge — an event, or an unjudged claim_kind;
            it writes a `similarity` edge, which **corroboration counts**, plus
            an `assessed` edge.
            Use "distinct" when they merely look alike; it writes `assessed`
            only, and writes nothing corroboration reads. Recording a decline as
            a similarity is how a graph starts manufacturing its own support, so
            reach for "one_claim" only when you would have merged.
            Either way the pair stops being nominated. `because` is required.
            Pairs that could not be recorded come back in `similarities_refused`
            with a reason — a cross-frame pair wants record_variant instead.
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
            pending_review by superseding the losing node with an existing one.
            Each: {old_id: str, by_id: str, because: str}, where `because` is
            "it_was_wrong" (a correction) or "the_world_changed" (the old claim
            still holds of its period — a renamed city, a change of government).
            There is no default: filing a world-change as an error is how the
            graph forgets its own history. Atomic; the winner is unchanged and
            dependent inferences are flagged evidence_stale.
        archivals: Archive trivial nodes from reflect's archival_candidates, as
            a list of node ids. **Ask the user before passing anything here** —
            archival is a human-approved verdict, like resolving a
            contradiction. Nothing is deleted: the response carries an
            archive_data export, and `restore` puts a node back. Never archive
            an inference on its own initiative; a flagged one means its evidence
            changed, which is a reason to re-derive it.
        judgments: Re-judge importance, typically for `stale_judgment` nominees
            from reflect's archival_candidates. Each: {node_id: str, direction:
            "up"|"down", reason: str, related_id: str | None}. Unlike archivals
            this is yours to decide — a change of degree, not a status verdict —
            and it is the right answer whenever a nominee should be kept but no
            longer treated as important. Judging it up is equally valid and
            equally useful: either way the judgment clock moves and the node
            leaves the stale set.
        relation_merges: Consolidate synonymous user relationship labels from
            reflect's similar_relations. Each: {labels: [str], into: str}. Every
            user-tier edge with a listed label is relabelled to `into`, in place.
        boundaries: Accept boundary proposals from reflect's
            `boundary_proposals` — where one claim's period ends and the next
            begins. Each: {node_id, source_id, endpoint: "start"|"end", at: ISO
            datetime, timeline_id: str | None}, copied from the proposal you
            agree with. **Read the proposal's `current` and `proposed` before
            passing it**: the period's basis becomes `inferred`, so an interval
            whose other end a document *stated* stops being reportable as
            stated. Change nothing you cannot defend from the two documents
            named — a date you know and neither document gives must not be
            passed here. Requests that no longer name exactly one open period
            come back under `boundaries_refused` with a reason rather than being
            applied to something adjacent.
        merge_similarity_threshold: Minimum pairwise cosine similarity required
            to allow a merge (default 0.92, deliberately high).
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
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
            judgments=judgments,
            relation_merges=relation_merges,
            boundaries=boundaries,
            similarities=similarities,
            merge_similarity_threshold=merge_similarity_threshold,
            judge=judge,
        ),
        ctx,
        f"parents={len(parents or [])} splits={len(splits or [])} "
        f"enrichments={len(enrichments or [])} merges={len(merges or [])} "
        f"supersessions={len(supersessions or [])} archivals={len(archivals or [])} "
        f"judgments={len(judgments or [])} "
        f"relation_merges={len(relation_merges or [])} "
        f"boundaries={len(boundaries or [])} "
        f"similarities={len(similarities or [])}",
        # A refusal should reach the operator's log, not only the agent's
        # response — a decision silently not recorded is the whole of #64.
        lambda r, m: (
            f"applied={m.nodes_returned}"
            + (
                f" similarities_refused={len(r['similarities_refused'])}"
                if r["similarities_refused"] else ""
            )
        ),
    )


@mcp.tool(name="review")
async def epimemer_review(
    ctx: Context,
    mode: str = "all",
    max_results: int = tools.REVIEW_MAX_RESULTS,
) -> str:
    """Read this graph's decision journal back, shakiest decisions first.

    Every judgment the graph has recorded — what was decided, about which nodes,
    by whom, and when — ordered so the calls most worth a second look arrive at
    the top. Read-only: acting on what you find goes through the ordinary
    decision tools.

    Ordering is two tiers and never one blended score. A decision whose agent
    declared a low `certainty` comes first; everything unrated follows, ordered
    by how many *derived* difficulty signals it carries:

    - `thin_source` — a subject's own `confidence` is below 0.5
    - `wide_merge` — three or more sources collapsed into one node
    - `open_contradiction` — recorded, both sides still active
    - `ground_moved` — a subject was retired after the decision was made

    An unrated decision never outranks one an agent actually flagged: a blank
    `certainty` means *unrated*, not *doubtful*.

    Read `unrated_count` beside the results — three shaky rows out of four
    hundred unrated is not the same answer as three out of four. `truncated`
    says the list was cut at `max_results`; act on what came back and review
    again rather than raising the number.

    **The answer covers this graph only, and `graph` names which.** For another,
    `use_graph` and ask again.

    Args:
        mode: Which decisions to look at. Only "all" is implemented so far.
        max_results: Cap on decisions returned (default 200).
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.review",
        lambda: tools.review(
            storage=deps["storage"], mode=mode, max_results=max_results,
        ),
        ctx,
        f"mode={mode} max_results={max_results}",
        lambda r, m: (
            f"refused mode={mode}" if "refused" in r else
            f"decisions={m.nodes_returned}/{r['decisions_scanned']} "
            f"graph={r['graph']} unrated={r['unrated_count']}"
            + (" truncated" if r["truncated"] else "")
        ),
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


@mcp.tool(name="graph_as_of")
async def memory_graph_as_of(
    at: str,
    ctx: Context,
    node_types: list[str] | None = None,
) -> str:
    """Snapshot what the graph *held* at a past instant.

    Returns the nodes that existed and were still active at `at` (an ISO
    datetime, normalized to UTC). This is a node-lifecycle snapshot only — edges,
    metacontext, and review labels are not time-versioned and are omitted, since
    they would reflect the present graph rather than the graph at `at`. For the
    *changes* across a span (births + retirements), use query_changes instead.

    **This is not "what was true then".** It answers *what did this memory
    believe on that date* — a node created last week is absent from a snapshot of
    last year even if the claim it makes has been true for a century. For when a
    claim was true, use `search` with `valid_as_of`.

    Args:
        at: ISO datetime to snapshot at.
        node_types: Optional filter to "topic"/"fact"/"inference".
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.graph_as_of",
        lambda: tools.graph_as_of(
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
    metacontext/review labels. Distinct from `graph_as_of`, which snapshots state
    at a single instant; this reports the *deltas* across a span.

    Each event carries a `kind` — created, corrected, historical, merged,
    archived, restored — and, where something replaced the node, the
    `counterpart` id that did. A node that retired, came back and retired again
    reports all three events, not only the last.

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
    ctx: Context,
    archive_data: dict | None = None,
    node_ids: list[str] | None = None,
    sourced_from: str | None = None,
    validity: list[dict] | None = None,
    expected_graph: str | None = None,
) -> str:
    """Bring nodes back — from an archive, or when a retired claim is true again.

    Two uses. Pass `archive_data` to reverse an `archive` sweep or reimport
    cold storage. Pass `node_ids` to **reactivate** a claim a new source says is
    true again: this is how you record the `recurs` verdict from
    check_conflicts, where a candidate whose status is `historical` says the
    same thing your new fact does. Labour out of government in 2010 and back in
    2024 is one claim recurring — reactivate it rather than storing a second
    node that says what the first one said.

    A node whose status is `corrected` cannot come back. It was retired for
    being *wrong*, and there is no evidence that makes a past error un-happen;
    if the graph now says otherwise, supersede the correction instead.

    Args:
        archive_data: The archive dict (as returned by archive).
        node_ids: Nodes to reactivate by id. Anything already active is skipped.
        sourced_from: **Required when reactivating a `historical` node** — the
            document id that asserts the claim again. A claim the graph states
            but cannot attribute is exactly what this system exists not to
            produce. It gets a new `sourced_from` edge, written in the same
            transaction as the reactivation; the node's earlier provenance and
            its lineage record are left untouched.
        expected_graph: The graph you believe you are working in. An archive blob
            carries its own content, so nothing in it names a graph and it will
            restore into whichever one is active — pass this and a mismatch is
            refused instead.
        validity: What that document says about *when* the claim is true again,
            in the same form `store_decomposition` takes. Omit it when the
            document gives no dates — the common case, and better than a guess.
    """
    deps = ctx.lifespan_context
    judge, refused = await _judge_for_write(ctx)
    if refused is not None:
        return refused
    return await _run_with_timeout(
        "epimemer.restore",
        lambda: tools.restore(
            storage=deps["storage"],
            archive_data=archive_data,
            node_ids=node_ids,
            sourced_from=sourced_from,
            validity=validity,
            expected_graph=expected_graph,
            judge=judge,
        ),
        ctx,
        f"nodes={len((archive_data or {}).get('nodes', []))} "
        f"reactivate={len(node_ids or [])}",
        lambda r, m: f"restored={r['nodes_restored']}",
    )


# --- Timeline tools ---


@mcp.tool(name="create_timeline")
async def memory_create_timeline(
    name: str,
    ctx: Context,
    description: str = "",
    reference_time: str | None = None,
) -> str:
    """Create a new timeline for tracking temporal relationships.

    Args:
        name: Name of the timeline (e.g., "History of AI").
        description: Optional description.
        reference_time: ISO-8601 instant that counts as this timeline's
            "now" — set it for a fictional or historical timeline whose present
            is not today ("the novel opens in May 1897"). Leave it unset for a
            timeline that tracks real time; that is not the same as passing
            today's date, which would freeze its present at this moment.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.create_timeline",
        lambda: tools.create_timeline(
            name=name,
            storage=deps["storage"],
            description=description,
            reference_time=_parse_utc(reference_time) if reference_time else None,
        ),
        ctx,
        f"name={name} reference_time={reference_time}",
        lambda r, m: f"id={r['timeline_id']}",
    )


@mcp.tool(name="set_reference_time")
async def memory_set_reference_time(
    timeline_id: str,
    ctx: Context,
    reference_time: str | None = None,
) -> str:
    """Set or clear a timeline's "now".

    A timeline's reference time is what a reader centres on and measures past
    and future against. Set it once you know when a fictional or historical
    timeline is anchored — that is usually after ingesting enough of the source
    to say, which is why this is separate from create_timeline.

    Args:
        timeline_id: The timeline to anchor.
        reference_time: ISO-8601 instant. **Omit to clear it**, returning the
            timeline to real time.
    """
    deps = ctx.lifespan_context
    return await _run_with_timeout(
        "epimemer.set_reference_time",
        lambda: tools.set_reference_time(
            timeline_id=timeline_id,
            storage=deps["storage"],
            reference_time=_parse_utc(reference_time) if reference_time else None,
        ),
        ctx,
        f"timeline={timeline_id} reference_time={reference_time}",
        lambda r, m: f"reference_time={r['reference_time']}",
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


# Where this session's judge lives. Session-scoped and JSON only (FastMCP
# serializes it), so the `JudgeRef` goes in as a dict and comes back validated —
# never held in a module global, which is the whole of §3.2: two graphs or two
# sessions must not be able to inherit each other's judge.
JUDGE_STATE_KEY = "epimemer.judge"


async def _bound_judge(ctx: Context) -> JudgeRef | None:
    """The judge bound to this session, or None if nothing has claimed one.

    Session state needs a session: called outside a request context — a direct
    invocation, or a transport that has not opened one — FastMCP raises rather
    than returning nothing. No session is genuinely no binding, so that reads as
    None here. It must not read as an error, or a graph switch would fail over
    an identity feature the caller never used.

    **Approval is re-checked here, on every write.** `use_graph` checks too, and
    this is what keeps that from being a single point of failure (§10.3): a
    revoked id, or a graph reached by any route that check missed, must not go
    on stamping decisions. A judge the active graph does not approve reads as
    *unknown* rather than raising — recording the name would assert an approval
    that no longer exists, and refusing would be the graph-level policy talking,
    which is not this function's to hold (§3.3).
    """
    try:
        stored = await ctx.get_state(JUDGE_STATE_KEY)
    except RuntimeError:
        # No session to read from, so fall back to the one this process was
        # told about (see `_bind_judge`). Reachable only where session state
        # does not exist at all, which today means a single-client transport —
        # so "the process" and "the client" are the same thing, and this is not
        # a shared binding two callers could confuse. It is per-server state
        # passed through the lifespan, never a module global.
        stored = ctx.lifespan_context.get("fallback_judge")
    if stored is None:
        return None
    judge = JudgeRef.model_validate(stored)
    if not await tools.judge_is_approved(ctx.lifespan_context["storage"], judge):
        _tool_logger.warning(
            "judge %r is not approved in graph %r; recording this write as "
            "unknown. Call claim_agent again.",
            judge.agent_id,
            ctx.lifespan_context["storage"].current_database,
        )
        return None
    return judge


async def _bind_judge(ctx: Context, judge: JudgeRef | None) -> bool:
    """Bind (or clear) this session's judge. False if there is no session.

    Reported rather than swallowed: everything downstream resolves the judge
    from here (§3.2), so a claim that recorded the agent but bound nothing is a
    state the caller has to be able to see.
    """
    payload = None if judge is None else judge.model_dump(mode="json")
    try:
        await ctx.set_state(JUDGE_STATE_KEY, payload)
    except RuntimeError:
        # Nowhere session-scoped to put it. Held on the lifespan instead, which
        # is what makes the require-a-judge setting usable from a transport
        # that has no sessions — otherwise turning it on would refuse every
        # write from such a client, with an identity it had correctly claimed.
        ctx.lifespan_context["fallback_judge"] = payload
        return False
    ctx.lifespan_context["fallback_judge"] = None
    return True


async def _judge_for_write(ctx: Context) -> tuple[JudgeRef | None, str | None]:
    """The judge for this write, or the refusal to return instead of doing it.

    One gate for every write path, and the only place the per-graph
    require-a-judge policy is read (§3.3.1). A backend that refused on its own
    account would be a second home for the policy, and the two could differ
    without anybody noticing.

    Absent and *permitted* is the default and not a degraded mode: the write
    goes through and records an unknown judge, which is what blank has always
    meant (§3.3).
    """
    deps = ctx.lifespan_context
    judge = await _bound_judge(ctx)
    if judge is not None:
        return judge, None
    if not await tools.judge_required(
        deps["storage"], process_default=deps["config"].require_judge
    ):
        return None, None
    approved = await deps["storage"].get_approved_agent_ids()
    return None, _error_response(tools.judge_required_reason(approved))


async def _elicit_agent_id(ctx: Context, agent_id: str, description: str) -> str | None:
    """Ask the **user** which id this agent may judge under (§2.3).

    `ctx.elicit` inverts the direction of an MCP call — the server asks, and the
    answer comes back from the user through their own client's UI. That is what
    lets `confirmed_at` mean what it says: no path exists by which the agent
    alone sets it.

    Every failure reads as *no answer*. A client without the elicitation
    capability raises here, and a raise has to refuse the claim rather than
    admit an id nobody approved — the one direction this must not fail in.
    """
    try:
        answer = await ctx.elicit(
            f"An agent asks to judge in graph "
            f"'{ctx.lifespan_context['storage'].current_database}' as "
            f"'{agent_id}'.\n\n"
            f"It describes itself as: {description}\n\n"
            f"The id is yours to assign — accept it, or edit it to whatever you "
            f"want this judge called. Decline to refuse. Nothing verifies the "
            f"description; it is what the agent says about itself, and it is "
            f"recorded as a claim rather than as a credential.",
            response_type=str,
        )
    except Exception:
        _tool_logger.info(
            "claim_agent: no elicitation channel to the user; refusing '%s'", agent_id
        )
        return None
    if isinstance(answer, AcceptedElicitation):
        # An accepted-but-empty answer is agreement with the prompt, which named
        # the proposed id. Reading it as a blank id would refuse the thing the
        # user just approved.
        return (answer.data or "").strip() or agent_id
    return None


async def _elicit_description_confirmation(
    ctx: Context, agent_id: str, description: str
) -> bool:
    """Ask the user to vouch for a *new* self-description under a known id.

    Softer than the id question by design: declining costs the confirmation, not
    the claim. The version is recorded either way, and *self-described,
    unconfirmed* is a different epistemic object rather than a failure (§2.4).
    """
    try:
        answer = await ctx.elicit(
            f"'{agent_id}' now describes itself as:\n\n{description}\n\n"
            f"Accept to record this description as confirmed by you. Decline and "
            f"it is still recorded, marked self-reported — only your "
            f"confirmation is at stake.",
            response_type=None,
        )
    except Exception:
        return False
    return isinstance(answer, AcceptedElicitation)


@mcp.tool(name="claim_agent")
async def memory_claim_agent(
    agent_id: str,
    description: str,
    ctx: Context,
) -> str:
    """Say which judge you are, so later review can tell your decisions apart.

    Propose an id and describe yourself; the **user** approves, and may hand
    back a different id. An id the user has not approved is refused — the id is
    theirs to assign, and that is what makes *"a different agent reviewed this"*
    something a graph can show rather than something an agent asserts.

    **Your description is a claim, not a credential.** Nothing verifies it. It
    is recorded like a fact you ingest, and a later reader is entitled to weigh
    it as self-reported prose. Describe what you are in a way that would let
    someone tell you from another agent — the model or harness you run in, the
    role you were given — and do not overstate it.

    Re-describing appends a version and never edits one, because a decision made
    last week was made by whatever you claimed to be last week. Approval is per
    graph, so switching graphs can unbind you; claim again after a use_graph.

    Args:
        agent_id: The id you propose to judge under. Ask the user what they want
            you called rather than inventing one; a refusal names the ids this
            graph already approved.
        description: What you are, in your own words. One or two sentences.
    """
    deps = ctx.lifespan_context

    async def claim() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.claim_agent(
            storage=deps["storage"],
            agent_id=agent_id,
            description=description,
            approve_id=lambda proposed, text: _elicit_agent_id(ctx, proposed, text),
            confirm_description=lambda claimed_id, text: (
                _elicit_description_confirmation(ctx, claimed_id, text)
            ),
        )
        if result["status"] == "claimed":
            # The binding is the point of the call, and it is written only after
            # the record is, so a failed upsert cannot leave a session judging
            # under an agent the graph does not have.
            result["session_bound"] = await _bind_judge(
                ctx, JudgeRef(agent_id=result["agent_id"], digest=result["digest"])
            )
        return result, meta

    return await _run_with_timeout(
        "epimemer.claim_agent",
        claim,
        ctx,
        f"agent_id={agent_id}",
        lambda r, m: (
            f"status={r['status']}"
            + (f" digest={r['digest']}" if r["status"] == "claimed" else "")
        ),
        # This call can be waiting on a person to read a prompt.
        waits_for_user=True,
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
    judge = await _bound_judge(ctx)

    async def switch() -> tuple[dict, ResponseMeta]:
        result, meta = await tools.use_graph(
            name=name,
            storage=deps["storage"],
            confirm=confirm,
            judge=judge,
            seed_agent_ids=deps["config"].approved_agents,
        )
        if result.get("judge_cleared"):
            await _bind_judge(ctx, None)
        return result, meta

    return await _run_with_timeout(
        "epimemer.use_graph",
        switch,
        ctx,
        f"name={name} confirm={confirm}",
        lambda r, m: (
            f"status={r.get('status', 'error')}"
            + (" judge_cleared" if r.get("judge_cleared") else "")
        ),
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
