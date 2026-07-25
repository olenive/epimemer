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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from epimemer.visualization.protocol import RpcRequest, SessionInfo

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

HUB_SERVICE = "epimemer-viz-hub"        # marker in /api/health, so a probe knows
DISCONNECT_GRACE_SECONDS = 300          # keep a dropped session listed (greyed) this long
RPC_TIMEOUT_SECONDS = 10.0

try:
    from importlib.metadata import version as _pkg_version

    VERSION = _pkg_version("epimemer")
except Exception:  # pragma: no cover - version lookup is best-effort
    VERSION = "dev"


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
      last_event_at, drop_handle}
    - ``browsers``: WebSocket -> {seq, session, graphs}  (per-connection seq +
      subscription to one session and an optional graph set)
    """
    sessions: dict[str, dict[str, Any]] = {}
    browsers: dict[WebSocket, dict[str, Any]] = {}

    # --- browser fan-out ---

    async def _fanout(message: dict, predicate: Callable[[dict], bool]) -> None:
        if not browsers:
            return
        dead: list[WebSocket] = []

        async def _one(ws: WebSocket, st: dict[str, Any]) -> None:
            if not predicate(st):
                return
            st["seq"] += 1
            try:
                await ws.send_text(json.dumps({**message, "seq": st["seq"]}))
            except Exception:
                dead.append(ws)

        await asyncio.gather(*[_one(ws, st) for ws, st in list(browsers.items())])
        for ws in dead:
            browsers.pop(ws, None)

    async def _broadcast_system(message: dict) -> None:
        """Send a system message (session up/down) to every browser."""
        await _fanout(message, lambda st: True)

    async def _broadcast_event(payload: dict) -> None:
        """Send an event to browsers subscribed to its session (and graph)."""
        session_id = payload.get("session_id")
        event_graph = payload.get("graph", "")

        def _match(st: dict[str, Any]) -> bool:
            if st["session"] is None or st["session"] != session_id:
                return False
            if st["graphs"] is not None and event_graph and event_graph not in st["graphs"]:
                return False
            return True

        await _fanout(payload, _match)

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
        except (WebSocketDisconnect, json.JSONDecodeError):
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
        }
        logger.info("Session registered: %s (%s:%s pid %s)", sid, info.backend, info.active_graph, info.pid)
        await _broadcast_system({"type": "session_connected", "session": _session_public(sid, sessions[sid])})

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
                    await _broadcast_event(payload)

                elif mtype == "register":
                    # active_graph (or other info) changed — update registry.
                    new_info = SessionInfo.model_validate(msg["info"])
                    sessions[sid]["info"] = new_info
                    await _broadcast_system(
                        {"type": "session_connected", "session": _session_public(sid, sessions[sid])}
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
                except (json.JSONDecodeError, TypeError):
                    continue
                sub = msg.get("subscribe")
                if isinstance(sub, dict):
                    st = browsers[ws]
                    st["session"] = sub.get("session")
                    graphs = sub.get("graphs")
                    st["graphs"] = set(graphs) if isinstance(graphs, list) else None
        except WebSocketDisconnect:
            pass
        finally:
            browsers.pop(ws, None)
            logger.info("Browser disconnected (%d remaining)", len(browsers))

    # --- HTTP endpoints ---

    async def api_health(request: Request) -> JSONResponse:
        return JSONResponse(
            {"ok": True, "service": HUB_SERVICE, "pid": os.getpid(), "version": VERSION,
             "sessions": len(sessions)}
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
            return JSONResponse({"error": "Missing 'session' or 'graph' query parameter"}, status_code=400)
        return await _rpc_endpoint(session_id, "snapshot", {"graph": graph})

    async def _rpc_endpoint(session_id: str, method: str, params: dict) -> JSONResponse:
        try:
            result = await _rpc(session_id, method, params)
        except LookupError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except asyncio.TimeoutError:
            return JSONResponse({"error": "session RPC timed out"}, status_code=504)
        except (ConnectionError, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(result)

    async def _index(request: Request) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    routes = [
        Route("/", _index),
        Route("/api/health", api_health),
        Route("/api/sessions", api_sessions),
        Route("/api/graphs", api_graphs),
        Route("/api/snapshot", api_snapshot),
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
    except (FileNotFoundError, ValueError):
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
    print(f"viz hub: running on {host}:{port} (pid {health.get('pid')}, version {health.get('version')})")
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
    group.add_argument("--status", action="store_true", help="Report hub + session status and exit.")
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
