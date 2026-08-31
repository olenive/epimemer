"""Standalone visualization hub — a relay + session registry.

The hub owns the viz port. MCP server processes are *sessions*: each dials out
to the hub (`hub_client.py`), registers, publishes events, and answers snapshot
/ graph-list RPCs against its own storage. Browsers connect to the hub, pick a
session from a selector, and view that session's graph live.

This replaces the old embedded per-process viz server (`ws_server.py`): the MCP
process never binds the port, so stale MCP orphans become dead sessions rather
than a stray server answering on the port with the wrong (empty) graph.

Run it:

    uv run epimemer-viz            # or: python -m epimemer.visualization.hub
    uv run epimemer-viz --status
    uv run epimemer-viz --stop

Normally an MCP server auto-spawns it on demand; the CLI is for explicit control.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from epimemer.visualization.protocol import RpcRequest, SessionInfo
from epimemer.visualization.ring import (
    LOG_RING_CAPACITY,
    RETRIEVAL_RING_CAPACITY,
    backfill,
    remember,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

HUB_SERVICE = "epimemer-viz-hub"  # marker in /api/health, so a probe knows
DISCONNECT_GRACE_SECONDS = 300  # keep a dropped session listed (greyed) this long
RPC_TIMEOUT_SECONDS = 10.0

# The coarse acts the log reads. Selected by `event_type` rather than by
# category: every graph event carries `category: graph`, so a category test
# would remember the firehose EVENT_LOG.md §3 refused to ship.
ACTION_EVENT_TYPE = "graph_action_recorded"

# Retrieval records, mirrored here so the selector, focus mode and the Response
# tab survive the session's death — the hub keeps disconnected sessions, but
# RPC to one raises, which is exactly the "open the dashboard after noticing"
# case (RETRIEVAL_PROVENANCE.md §3.2). What arrives is already guarded: on a
# non-loopback bind the session sends structural metadata only.
RETRIEVAL_EVENT_TYPE = "retrieval_recorded"

# Both rings hang off `sessions[sid]`; this says which event fills which, and
# how deep. Keeping the pair in one place is what stops a third ring arriving
# with a capacity nobody chose.
_RING_KEYS = {ACTION_EVENT_TYPE: "actions", RETRIEVAL_EVENT_TYPE: "retrievals"}


def _ring_capacity(event_type: str) -> int:
    """Read at call time, not captured — the capacities are module constants a
    test may substitute, and a table built at import would not see it."""
    return LOG_RING_CAPACITY if event_type == ACTION_EVENT_TYPE else RETRIEVAL_RING_CAPACITY


try:
    from importlib.metadata import version as _pkg_version

    VERSION = _pkg_version("epimemer")
except Exception:  # pragma: no cover - version lookup is best-effort
    VERSION = "dev"


def _now() -> datetime:
    return datetime.now(UTC)


def _pidfile_path() -> Path:
    return Path.home() / ".epimemer" / "viz-hub.pid"


# ----------------------------------------------------------------------------
# The app: relay + registry, built functionally over closures.
# ----------------------------------------------------------------------------


def create_hub_app() -> Starlette:
    """Build the hub's Starlette app.

    State lives in two dicts captured by the route closures (single event loop,
    so no locks beyond what each await naturally serializes):

    - ``sessions``: session_id -> {ws, info, pending_rpcs, connected,
      last_event_at, drop_handle, actions}
    - ``browsers``: WebSocket -> {seq, session, graphs}  (per-connection seq +
      subscription to one session and an optional graph set)

    ``actions`` is the log's bounded ring (EVENT_LOG.md §4.2). It hangs off the
    session because every event already passes through one line here, where the
    hub stamps ``session_id`` before fan-out — so the append is one statement at
    a place that already exists, and a browser gets backfill on subscribe with
    no RPC round-trip and without waking a session process.

    Stated plainly, because it is easy to oversell: this survives browser
    reloads, which is the case that matters. It does **not** survive an MCP
    restart — ``session_id`` is a fresh uuid4 per process, so a restarted server
    registers as a different session with an empty ring. Only ``query_changes``
    (§6) is durable.
    """
    sessions: dict[str, dict[str, Any]] = {}
    browsers: dict[WebSocket, dict[str, Any]] = {}

    # --- browser fan-out ---

    async def _send(ws: WebSocket, st: dict[str, Any], message: dict) -> bool:
        """One message to one browser, stamped with that browser's own `seq`.

        `seq` is per connection and starts at 0, so it detects a drop on this
        socket and says nothing about position in any stream — which is why a
        log entry carries its own `action_id` (§4.1).
        """
        st["seq"] += 1
        try:
            await ws.send_text(json.dumps({**message, "seq": st["seq"]}))
            return True
        except Exception:
            return False

    async def _fanout(message: dict, predicate: Callable[[dict], bool]) -> None:
        if not browsers:
            return
        targets = [(ws, st) for ws, st in browsers.items() if predicate(st)]
        alive = await asyncio.gather(*[_send(ws, st, message) for ws, st in targets])
        for (ws, _), ok in zip(targets, alive, strict=True):
            if not ok:
                browsers.pop(ws, None)

    async def _broadcast_system(message: dict) -> None:
        """Send a system message (session up/down) to every browser."""
        await _fanout(message, lambda st: True)

    def _subscribed(payload: dict) -> Callable[[dict[str, Any]], bool]:
        """Does a browser's subscription cover this payload's session and graph?"""
        session_id = payload.get("session_id")
        event_graph = payload.get("graph", "")

        def _match(st: dict[str, Any]) -> bool:
            if st["session"] is None or st["session"] != session_id:
                return False
            if st["graphs"] is not None and event_graph and event_graph not in st["graphs"]:
                return False
            return True

        return _match

    async def _broadcast_event(payload: dict) -> None:
        """Send an event to browsers subscribed to its session (and graph)."""
        await _fanout(payload, _subscribed(payload))

    async def _replay(ws: WebSocket, st: dict[str, Any]) -> None:
        """Hand one browser the acts and records it missed, oldest first.

        The same subscription test the live path uses, so a replayed entry can
        never reach a browser a live one would not have — an entry from graph A
        must not highlight into graph B (EVENT_LOG.md §6). Per-session by
        construction: the ring hangs off the session, so records from two
        sessions cannot mix however they interleave in time.
        """
        sess = sessions.get(st["session"] or "")
        if sess is None:
            return
        for ring_key in _RING_KEYS.values():
            for payload in backfill(sess[ring_key]):
                if _subscribed(payload)(st) and not await _send(ws, st, payload):
                    browsers.pop(ws, None)
                    return

    def _session_public(sid: str, sess: dict[str, Any]) -> dict:
        info: SessionInfo = sess["info"]
        return {
            **info.model_dump(mode="json"),
            "connected": sess["connected"],
            "last_event_at": sess["last_event_at"].isoformat() if sess["last_event_at"] else None,
        }

    # --- RPC to a session ---

    async def _rpc(session_id: str, method: str, params: dict) -> dict:
        sess = sessions.get(session_id)
        if sess is None or not sess["connected"]:
            raise LookupError(f"session {session_id!r} not connected")

        request_id = uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        sess["pending_rpcs"][request_id] = fut
        try:
            await sess["ws"].send_text(
                RpcRequest(request_id=request_id, method=method, params=params).model_dump_json()
            )
            return await asyncio.wait_for(fut, timeout=RPC_TIMEOUT_SECONDS)
        finally:
            sess["pending_rpcs"].pop(request_id, None)

    # --- session (ingest) socket ---

    async def handle_ingest(ws: WebSocket) -> None:
        await ws.accept()
        # First message must be a Register.
        try:
            first = json.loads(await ws.receive_text())
        except WebSocketDisconnect, json.JSONDecodeError:
            await ws.close(code=1002)
            return
        if first.get("type") != "register":
            await ws.close(code=1002)
            return

        info = SessionInfo.model_validate(first["info"])
        sid = info.session_id

        # Reconnect of a known session: cancel any pending drop and reuse the slot.
        existing = sessions.get(sid)
        if existing is not None and existing.get("drop_handle") is not None:
            existing["drop_handle"].cancel()

        sessions[sid] = {
            "ws": ws,
            "info": info,
            "pending_rpcs": {},
            "connected": True,
            "last_event_at": existing["last_event_at"] if existing else None,
            "drop_handle": None,
            # A reconnecting session keeps both rings: the process is the same
            # one, its ids carry on, and dropping them here would empty the
            # panel on every hub blip.
            "actions": existing["actions"] if existing else (),
            "retrievals": existing["retrievals"] if existing else (),
        }
        logger.info(
            "Session registered: %s (%s:%s pid %s)", sid, info.backend, info.active_graph, info.pid
        )
        await _broadcast_system(
            {"type": "session_connected", "session": _session_public(sid, sessions[sid])}
        )

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")

                if mtype == "event":
                    payload = dict(msg.get("payload", {}))
                    payload["session_id"] = sid
                    sessions[sid]["last_event_at"] = _now()
                    ring_key = _RING_KEYS.get(payload.get("event_type", ""))
                    if ring_key is not None:
                        sessions[sid][ring_key] = remember(
                            sessions[sid][ring_key],
                            payload,
                            capacity=_ring_capacity(payload["event_type"]),
                        )
                    await _broadcast_event(payload)

                elif mtype == "register":
                    # active_graph (or other info) changed — update registry.
                    new_info = SessionInfo.model_validate(msg["info"])
                    sessions[sid]["info"] = new_info
                    await _broadcast_system(
                        {
                            "type": "session_connected",
                            "session": _session_public(sid, sessions[sid]),
                        }
                    )

                elif mtype == "rpc_response":
                    fut = sessions[sid]["pending_rpcs"].get(msg.get("request_id"))
                    if fut is not None and not fut.done():
                        if msg.get("error"):
                            fut.set_exception(RuntimeError(msg["error"]))
                        else:
                            fut.set_result(msg.get("result") or {})
        except WebSocketDisconnect:
            pass
        finally:
            await _mark_disconnected(sid, ws)

    async def _mark_disconnected(sid: str, ws: WebSocket) -> None:
        sess = sessions.get(sid)
        # Only act if this ws is still the registered one (guards races with a reconnect).
        if sess is None or sess["ws"] is not ws:
            return
        sess["connected"] = False
        # Fail any in-flight RPCs so callers get a prompt error, not a timeout.
        for fut in sess["pending_rpcs"].values():
            if not fut.done():
                fut.set_exception(ConnectionError("session disconnected"))
        sess["pending_rpcs"].clear()

        loop = asyncio.get_running_loop()

        def _drop() -> None:
            cur = sessions.get(sid)
            if cur is not None and not cur["connected"]:
                sessions.pop(sid, None)
                asyncio.ensure_future(
                    _broadcast_system({"type": "session_dropped", "session_id": sid})
                )

        sess["drop_handle"] = loop.call_later(DISCONNECT_GRACE_SECONDS, _drop)
        logger.info("Session disconnected: %s", sid)
        await _broadcast_system({"type": "session_disconnected", "session_id": sid})

    # --- browser socket ---

    async def handle_browser(ws: WebSocket) -> None:
        await ws.accept()
        browsers[ws] = {"seq": 0, "session": None, "graphs": None}
        logger.info("Browser connected (%d total)", len(browsers))
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError, TypeError:
                    continue
                sub = msg.get("subscribe")
                if isinstance(sub, dict):
                    st = browsers[ws]
                    st["session"] = sub.get("session")
                    graphs = sub.get("graphs")
                    st["graphs"] = set(graphs) if isinstance(graphs, list) else None
                    # You open the dashboard *after* noticing the agent did
                    # something, so a live-only stream is empty exactly when it
                    # is wanted (EVENT_LOG.md §4.2).
                    await _replay(ws, st)
        except WebSocketDisconnect:
            pass
        finally:
            browsers.pop(ws, None)
            logger.info("Browser disconnected (%d remaining)", len(browsers))

    # --- HTTP endpoints ---

    async def api_health(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": HUB_SERVICE,
                "pid": os.getpid(),
                "version": VERSION,
                "sessions": len(sessions),
            }
        )

    async def api_sessions(request: Request) -> JSONResponse:
        return JSONResponse([_session_public(sid, s) for sid, s in sessions.items()])

    async def api_graphs(request: Request) -> JSONResponse:
        session_id = request.query_params.get("session")
        if not session_id:
            return JSONResponse({"error": "Missing 'session' query parameter"}, status_code=400)
        return await _rpc_endpoint(session_id, "list_graphs", {})

    async def api_snapshot(request: Request) -> JSONResponse:
        session_id = request.query_params.get("session")
        graph = request.query_params.get("graph")
        if not session_id or not graph:
            return JSONResponse(
                {"error": "Missing 'session' or 'graph' query parameter"}, status_code=400
            )
        return await _rpc_endpoint(session_id, "snapshot", {"graph": graph})

    async def api_retrievals(request: Request) -> JSONResponse:
        """This session's retrieval records, payloads included.

        Served by the session process itself, which is what makes it work when
        the hub's own mirror is guarded down to structural metadata. It stops
        working when the session exits — the mirror is what survives that, and
        the two are complementary rather than redundant (§3.2).
        """
        session_id = request.query_params.get("session")
        if not session_id:
            return JSONResponse({"error": "Missing 'session' query parameter"}, status_code=400)
        return await _rpc_endpoint(session_id, "retrievals", {})

    async def _rpc_endpoint(session_id: str, method: str, params: dict) -> JSONResponse:
        try:
            result = await _rpc(session_id, method, params)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except TimeoutError:
            return JSONResponse({"error": "session RPC timed out"}, status_code=504)
        except (ConnectionError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(result)

    async def _index(request: Request) -> FileResponse | PlainTextResponse:
        # The bundle is build output and never committed, so a checkout has
        # none until it is built. The API and both sockets work without it;
        # the page is the only thing missing, and this says how to get it.
        index = STATIC_DIR / "index.html"
        if not index.exists():
            return PlainTextResponse(
                "The epimemer visualization page is not built.\n\n"
                "This hub is serving its API and websockets, but the browser\n"
                "bundle is absent. Build it with `make build-frontend` (or\n"
                "`npm ci && npm run build` in epimemer/visualization/frontend)\n"
                "and reload. A released wheel carries the bundle already.\n",
                status_code=503,
            )
        return FileResponse(index)

    routes = [
        Route("/", _index),
        Route("/api/health", api_health),
        Route("/api/sessions", api_sessions),
        Route("/api/graphs", api_graphs),
        Route("/api/snapshot", api_snapshot),
        Route("/api/retrievals", api_retrievals),
        WebSocketRoute("/ws", handle_browser),
        WebSocketRoute("/ingest", handle_ingest),
    ]
    if STATIC_DIR.exists():
        routes.append(Mount("/", app=StaticFiles(directory=STATIC_DIR), name="static"))

    return Starlette(routes=routes)


# ----------------------------------------------------------------------------
# CLI / process lifecycle
# ----------------------------------------------------------------------------


def _hub_host_port() -> tuple[str, int]:
    return (
        os.environ.get("EPIMEMER_VIZ_HOST", "127.0.0.1"),
        int(os.environ.get("EPIMEMER_VIZ_PORT", "8765")),
    )


def _try_bind(host: str, port: int) -> socket.socket | None:
    """Bind (host, port) and return the socket, or None if the port is taken."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        return None
    return sock


def probe_health(host: str, port: int, timeout: float = 0.5) -> dict | None:
    """Return the health JSON iff an epimemer hub answers on (host, port)."""
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (localhost)
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("service") == HUB_SERVICE else None


def _get_json(url: str, timeout: float = 1.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (localhost)
            return json.loads(resp.read().decode())
    except Exception:
        return None


def hub_sessions(host: str, port: int, timeout: float = 1.0) -> list | None:
    """The hub's live session list, or None if the hub is unreachable."""
    return _get_json(f"http://{host}:{port}/api/sessions", timeout)


def _spawn_detached_hub(host: str, port: int) -> None:
    """Launch a hub in a new session so it outlives this MCP process."""
    import subprocess

    env = dict(os.environ)
    env["EPIMEMER_VIZ_HOST"] = host
    env["EPIMEMER_VIZ_PORT"] = str(port)

    log_file = env.get("EPIMEMER_LOG_FILE")
    out = open(log_file, "a") if log_file else subprocess.DEVNULL  # noqa: SIM115
    try:
        subprocess.Popen(
            [sys.executable, "-m", "epimemer.visualization.hub"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )
    finally:
        if log_file:
            out.close()


async def ensure_hub_running(
    host: str, port: int, *, autospawn: bool = True, wait: float = 3.0
) -> bool:
    """Return True if a hub is reachable, auto-spawning a detached one if not.

    The health probe is blocking, so it runs off the event loop. If two MCP
    processes race to spawn, the loser's hub exits cleanly on the bind conflict
    (see ``run``) — harmless.
    """
    if await asyncio.to_thread(probe_health, host, port) is not None:
        return True
    if not autospawn:
        return False
    _spawn_detached_hub(host, port)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        await asyncio.sleep(0.15)
        if await asyncio.to_thread(probe_health, host, port) is not None:
            return True
    return False


def _write_pidfile() -> None:
    path = _pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))


def _remove_pidfile() -> None:
    try:
        _pidfile_path().unlink()
    except FileNotFoundError:
        pass


def _read_pidfile() -> int | None:
    try:
        return int(_pidfile_path().read_text().strip())
    except FileNotFoundError, ValueError:
        return None


def run() -> int:
    """Bind the port and serve the hub, forever. Returns a process exit code."""
    host, port = _hub_host_port()
    sock = _try_bind(host, port)
    if sock is None:
        # Someone already holds the port. Lost the spawn race iff it's a hub.
        if probe_health(host, port) is not None:
            print(f"epimemer viz hub already running on {host}:{port}", file=sys.stderr)
            return 0
        print(
            f"port {host}:{port} is held by a non-epimemer process; refusing to start",
            file=sys.stderr,
        )
        return 1

    _write_pidfile()
    logger.info("Visualization hub listening on http://%s:%d", host, port)
    config = uvicorn.Config(create_hub_app(), log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    try:
        asyncio.run(server.serve(sockets=[sock]))
    finally:
        _remove_pidfile()
    return 0


def status() -> int:
    host, port = _hub_host_port()
    health = probe_health(host, port, timeout=1.0)
    if health is None:
        print(f"viz hub: not running (nothing healthy on {host}:{port})")
        return 1
    print(
        f"viz hub: running on {host}:{port} "
        f"(pid {health.get('pid')}, version {health.get('version')})"
    )
    sessions = _get_json(f"http://{host}:{port}/api/sessions") or []
    if not sessions:
        print("  sessions: none")
    for s in sessions:
        state = "connected" if s.get("connected") else "disconnected"
        print(
            f"  - {s.get('backend')}:{s.get('active_graph')} "
            f"(pid {s.get('pid')}, session {str(s.get('session_id'))[:8]}, {state})"
        )
    return 0


def stop() -> int:
    pid = _read_pidfile()
    if pid is None:
        print("viz hub: no pidfile; nothing to stop", file=sys.stderr)
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print("viz hub: process not running; clearing stale pidfile")
        _remove_pidfile()
        return 0
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            _remove_pidfile()
            print(f"viz hub stopped (pid {pid})")
            return 0
        time.sleep(0.1)
    print(f"viz hub: sent SIGTERM to pid {pid}; still shutting down", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="epimemer-viz", description="Epimemer visualization hub.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--status", action="store_true", help="Report hub + session status and exit."
    )
    group.add_argument("--stop", action="store_true", help="Stop a running hub and exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("EPIMEMER_LOG_LEVEL", "INFO"))
    if args.status:
        return status()
    if args.stop:
        return stop()
    return run()


if __name__ == "__main__":
    sys.exit(main())
