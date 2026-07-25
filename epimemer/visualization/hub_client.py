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
from typing import Awaitable, Callable

from websockets.asyncio.client import connect as ws_connect

from epimemer.storage.protocol import StorageBackend
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import Event
from epimemer.visualization.protocol import (
    PublishEvent,
    Register,
    RpcRequest,
    RpcResponse,
    SessionInfo,
)
from epimemer.visualization.snapshot import assemble_snapshot, list_graphs_result

logger = logging.getLogger(__name__)

_MAX_BACKOFF = 30.0
_QUEUE_MAXSIZE = 1000


async def start_hub_client(
    bus: InProcessEventBus,
    raw_storage: StorageBackend,
    info: SessionInfo,
    hub_url: str,
) -> Callable[[], Awaitable[None]]:
    """Start forwarding this session to the hub. Returns an async ``stop()``.

    ``raw_storage`` is the *pre-instrumentation* backend, used to answer RPC
    reads without emitting further events. ``hub_url`` is the ``/ingest``
    endpoint (e.g. ``ws://127.0.0.1:8765/ingest``).
    """
    stop_event = asyncio.Event()
    # RPC reads share one lock: two concurrent viz reads must not interleave the
    # active-database `use()` switches on a SurrealDB connection. This is the same
    # shared-connection hazard as ISSUES.md #16; the lock only serializes viz
    # reads against each other. #16 proper (a viz read racing a tool call on the
    # shared connection) stays deferred — the eventual fix (a dedicated read
    # connection for SurrealDB) would land right here in the RPC handler.
    read_lock = asyncio.Lock()

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
            async with read_lock:
                if req.method == "list_graphs":
                    result = await list_graphs_result(raw_storage)
                elif req.method == "snapshot":
                    result = await assemble_snapshot(raw_storage, req.params["graph"])
                else:
                    raise ValueError(f"unknown rpc method {req.method!r}")
            await ws.send(RpcResponse(request_id=req.request_id, result=result).model_dump_json())
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
        done, pending = await asyncio.wait(
            {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
        )
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
                async with ws_connect(hub_url) as ws:
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
