"""Session-side client that connects an MCP process to the visualization hub.

Each MCP server process runs one of these. It dials out to the hub's ``/ingest``
socket, registers this session, forwards every visualization event from the
in-process bus, and answers the hub's snapshot / graph-list RPCs by reading this
process's own storage — which is what makes an in-memory / ``mem://`` graph
viewable at all (the reads execute where the data lives).

Connection is best-effort: if the hub is down the client retries forever with
capped backoff, and events produced while disconnected are dropped (browsers
recover the full picture via a snapshot refresh). The MCP server never blocks on
the hub.
"""

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect as ws_connect

from epimemer.core.advisories import WarningPolicy
from epimemer.storage.protocol import StorageBackend
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import Event
from epimemer.visualization.protocol import (
    INGEST_MAX_MESSAGE_BYTES,
    VIZ_SCHEMA_VERSION,
    PublishEvent,
    Register,
    RpcRequest,
    RpcResponse,
    SessionInfo,
)
from epimemer.visualization.snapshot import (
    assemble_embeddings,
    assemble_snapshot,
    list_graphs_result,
    warning_settings_result,
)

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 30.0
_QUEUE_MAXSIZE = 1000

# An answer is sent only if it leaves this much of the ingest ceiling unused.
# The error that replaces one that does not is built from the method, a node
# type and two numbers, a few hundred bytes, so whichever reply goes, it fits.
_ANSWER_HEADROOM_BYTES = 4096


def _too_large(req: RpcRequest, size: int, budget: int) -> str:
    """Why an answer was not sent, and what to ask for instead.

    Nothing unbounded from the request is repeated: the method is one of the
    protocol's names, a node type has passed `NodeType` by the time an answer
    exists, and the graph name is left out because the caller sent it.
    """
    over = (
        f"the {req.method} answer would be {size:,} bytes, over the {budget:,} an "
        f"answer may take under the hub's {INGEST_MAX_MESSAGE_BYTES:,}-byte message ceiling"
    )
    version = f"schema version {VIZ_SCHEMA_VERSION}"
    if req.method == "embeddings":
        node_type = req.params.get("node_type")
        if node_type is None:
            return f"{over}; ask for one node_type: topic, fact or inference"
        return f"{over}; this graph has too many {node_type} vectors for one answer at {version}"
    if req.method == "snapshot":
        return f"{over}; this graph is too large for one snapshot at {version}"
    return over


def _reply(req: RpcRequest, result: dict) -> str:
    """The answer to `req`, or an error in its place if the answer would not fit.

    An answer over the ceiling does more than fail: the hub closes the socket
    with 1009, which drops the session and every RPC in flight on it.
    Measured in UTF-8 bytes, which is what the ceiling counts; node content is
    sent unescaped, so counting characters would undercount it.

    The ceiling is read at call time rather than captured, so a test can lower
    it without building a 16 MiB graph.
    """
    answer = RpcResponse(request_id=req.request_id, result=result).model_dump_json()
    size = len(answer.encode())
    budget = INGEST_MAX_MESSAGE_BYTES - _ANSWER_HEADROOM_BYTES
    if size <= budget:
        return answer
    reason = _too_large(req, size, budget)
    logger.warning("viz RPC answer not sent: %s", reason)
    return RpcResponse(request_id=req.request_id, error=reason).model_dump_json()


async def start_hub_client(
    bus: InProcessEventBus,
    raw_storage: StorageBackend,
    info: SessionInfo,
    hub_url: str,
    default_reflect_threshold: int = 10,
    default_warning_policy: WarningPolicy | None = None,
    records: Callable[[], list[dict]] | None = None,
) -> Callable[[], Awaitable[None]]:
    """Start forwarding this session to the hub. Returns an async ``stop()``.

    ``raw_storage`` is the *pre-instrumentation* backend, used to answer RPC
    reads without emitting further events. ``hub_url`` is the ``/ingest``
    endpoint (e.g. ``ws://127.0.0.1:8765/ingest``).
    ``default_reflect_threshold`` is the server default reported with the
    active graph's reflection pressure, unless that graph overrides it.
    ``default_warning_policy`` is the process-wide advisory policy the
    ``warnings`` RPC lays the active graph's overrides over, passed as a value
    like every other setting; omitting it reports the built-in policy, which is
    what ``configure_warnings`` reports in that case too.
    ``records`` returns this session's retrieval records, already serialized;
    the ``retrievals`` RPC answers from it, payloads included, which is the
    route that still works when the hub's mirror is guarded. It is a callable
    rather than the log itself so this module stays ignorant of the record
    type, exactly as ``PublishEvent.payload`` is opaque to the hub.
    """
    stop_event = asyncio.Event()
    # Every RPC read takes the storage guard's mover turn. Two things follow
    # from that one line. Snapshot reads do not interleave with each other, since
    # a mover excludes a mover. And they do not interleave with **tool calls**
    # either, which a lock of their own would not prevent: a snapshot of a graph
    # this session is not on borrows the SurrealDB connection, and a write in
    # flight during that borrow would land in the graph being snapshotted.
    #
    # It is one turn for the whole handler rather than one per `viz_list_*`, so
    # every read behind a snapshot describes a single instant.
    read_turn = raw_storage.graph_guard.moving

    # Per-connection outbound queue; None while disconnected so the bus handler
    # drops events instead of buffering them across a dead connection.
    state: dict = {"queue": None, "info": info}

    def _on_event(event: Event) -> None:
        queue: asyncio.Queue | None = state["queue"]
        if queue is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(json.loads(event.model_dump_json()))

    unsubscribe = bus.subscribe(handler=_on_event)

    async def _handle_rpc(ws, req: RpcRequest) -> None:
        try:
            async with read_turn():
                if req.method == "list_graphs":
                    result = await list_graphs_result(raw_storage, default_reflect_threshold)
                elif req.method == "warnings":
                    result = await warning_settings_result(
                        raw_storage,
                        default_warning_policy
                        if default_warning_policy is not None
                        else WarningPolicy(),
                    )
                elif req.method == "snapshot":
                    result = await assemble_snapshot(raw_storage, req.params["graph"])
                elif req.method == "embeddings":
                    result = await assemble_embeddings(
                        raw_storage, req.params["graph"], req.params.get("node_type")
                    )
                elif req.method == "retrievals":
                    # No storage read, so the turn buys nothing here, but
                    # every RPC staying on one path is worth more than the
                    # microseconds, and a future record that *did* read
                    # storage would otherwise be the exception nobody noticed.
                    result = {"records": records() if records else []}
                else:
                    raise ValueError(f"unknown rpc method {req.method!r}")
            await ws.send(_reply(req, result))
        except Exception as exc:  # report the error back rather than hang the hub
            with contextlib.suppress(Exception):
                await ws.send(
                    RpcResponse(request_id=req.request_id, error=str(exc)).model_dump_json()
                )

    async def _sender(ws) -> None:
        queue: asyncio.Queue = state["queue"]
        while True:
            payload = await queue.get()
            # A graph switch updates our registered identity, so the hub's session
            # list and browser routing stay correct without parsing every event.
            if payload.get("event_type") == "graph_switched" and payload.get("new_graph"):
                state["info"] = state["info"].model_copy(
                    update={"active_graph": payload["new_graph"]}
                )
                await ws.send(Register(info=state["info"]).model_dump_json())
            await ws.send(PublishEvent(payload=payload).model_dump_json())

    async def _receiver(ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "rpc_request":
                asyncio.create_task(_handle_rpc(ws, RpcRequest.model_validate(msg)))

    async def _session_once(ws) -> None:
        await ws.send(Register(info=state["info"]).model_dump_json())
        sender = asyncio.create_task(_sender(ws))
        receiver = asyncio.create_task(_receiver(ws))
        done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            if task.exception() is not None:
                raise task.exception()

    async def _run() -> None:
        backoff = 1.0
        failure_logged = False
        while not stop_event.is_set():
            try:
                state["queue"] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
                async with ws_connect(hub_url, max_size=INGEST_MAX_MESSAGE_BYTES) as ws:
                    backoff = 1.0
                    if failure_logged:
                        logger.info("viz hub reachable again at %s", hub_url)
                        failure_logged = False
                    await _session_once(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not failure_logged:
                    # Surface once, loudly — Claude Code shows MCP-server stderr,
                    # unlike the log file — then stay quiet on subsequent retries.
                    logger.error(
                        "viz hub unreachable at %s (%s); run `uv run epimemer-viz --status`",
                        hub_url,
                        exc,
                    )
                    print(
                        f"[epimemer] viz hub unreachable at {hub_url}; "
                        f"run `uv run epimemer-viz --status`",
                        file=sys.stderr,
                    )
                    failure_logged = True
                else:
                    logger.debug("viz hub still unreachable at %s (%s)", hub_url, exc)
            finally:
                state["queue"] = None
            if stop_event.is_set():
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)

    task = asyncio.create_task(_run())

    async def stop() -> None:
        stop_event.set()
        unsubscribe()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    return stop
