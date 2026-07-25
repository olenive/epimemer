"""Tests for the visualization hub (relay + session registry).

The hub is inherently multi-connection, so these run it under a real uvicorn
server on a random port *inside the test event loop* — ingest sockets, browser
sockets, and HTTP RPC calls are then genuinely concurrent, which the synchronous
Starlette TestClient cannot express (an in-flight HTTP RPC must be answered by a
message the test drives onto the ingest socket).
"""

import asyncio
import json
import socket
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
import uvicorn
import websockets

from epimemer.visualization import hub as hubmod
from epimemer.visualization.hub import create_hub_app
from epimemer.visualization.protocol import PublishEvent, Register, RpcResponse, SessionInfo


def _session(session_id: str, *, backend: str = "surrealdb", graph: str = "memory") -> SessionInfo:
    return SessionInfo(session_id=session_id, pid=1234, backend=backend, active_graph=graph)


def _node_event(graph: str = "memory") -> dict:
    return {
        "category": "graph",
        "event_type": "node_stored",
        "graph": graph,
        "node": {"node_id": "n1", "content": "hi"},
    }


async def _http_get(addr: str, path: str, timeout: float = 10.0) -> tuple[int, str]:
    """Blocking GET run off-loop, so it can be awaited concurrently with ws work."""

    def _do() -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"http://{addr}{path}", timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    return await asyncio.to_thread(_do)


async def _wait(pred, timeout: float = 3.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


@pytest.fixture
async def hub():
    app = create_hub_app()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started, "hub server did not start"

    addr = f"127.0.0.1:{port}"
    try:
        yield SimpleNamespace(addr=addr, ingest=f"ws://{addr}/ingest", ws=f"ws://{addr}/ws")
    finally:
        server.should_exit = True
        await task


async def _register(ws, info: SessionInfo) -> None:
    await ws.send(Register(info=info).model_dump_json())


async def _sessions(addr: str) -> list[dict]:
    _, body = await _http_get(addr, "/api/sessions")
    return json.loads(body)


# --- registry ---


async def test_register_lists_session(hub):
    async with websockets.connect(hub.ingest) as ws:
        await _register(ws, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))
        sessions = await _sessions(hub.addr)
        assert sessions[0]["session_id"] == "s-a"
        assert sessions[0]["backend"] == "surrealdb"
        assert sessions[0]["active_graph"] == "memory"
        assert sessions[0]["connected"] is True


async def _pred_len(addr: str, n: int) -> bool:
    return len(await _sessions(addr)) == n


async def test_disconnect_marks_session_not_connected(hub):
    async with websockets.connect(hub.ingest) as ws:
        await _register(ws, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))
    # socket closed on exit; session stays listed (grace period) but disconnected

    async def _disconnected() -> bool:
        sessions = await _sessions(hub.addr)
        return len(sessions) == 1 and sessions[0]["connected"] is False

    await _wait(_disconnected)


# --- event fan-out ---


async def test_event_fans_out_with_session_id_and_seq(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        async with websockets.connect(hub.ws) as browser:
            await browser.send(json.dumps({"subscribe": {"session": "s-a", "graphs": None}}))
            await asyncio.sleep(0.1)  # let the subscription register

            await sess.send(PublishEvent(payload=_node_event()).model_dump_json())
            msg = json.loads(await asyncio.wait_for(browser.recv(), timeout=3))

            assert msg["event_type"] == "node_stored"
            assert msg["session_id"] == "s-a"
            assert msg["seq"] == 1


async def test_browser_only_receives_its_subscribed_session(hub):
    async with websockets.connect(hub.ingest) as sess_a, websockets.connect(hub.ingest) as sess_b:
        await _register(sess_a, _session("s-a"))
        await _register(sess_b, _session("s-b"))
        await _wait(lambda: _pred_len(hub.addr, 2))

        async with websockets.connect(hub.ws) as browser:
            await browser.send(json.dumps({"subscribe": {"session": "s-a", "graphs": None}}))
            await asyncio.sleep(0.1)

            # An event from the *other* session must not reach this browser.
            await sess_b.send(PublishEvent(payload=_node_event()).model_dump_json())
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(browser.recv(), timeout=0.5)

            # ...but an event from the subscribed session does.
            await sess_a.send(PublishEvent(payload=_node_event()).model_dump_json())
            msg = json.loads(await asyncio.wait_for(browser.recv(), timeout=3))
            assert msg["session_id"] == "s-a"


# --- RPC round-trip ---


async def test_snapshot_rpc_round_trip(hub):
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        get_task = asyncio.create_task(
            _http_get(hub.addr, "/api/snapshot?session=s-a&graph=memory")
        )
        req = json.loads(await asyncio.wait_for(sess.recv(), timeout=3))
        assert req["type"] == "rpc_request"
        assert req["method"] == "snapshot"
        assert req["params"]["graph"] == "memory"

        canned = {"graph": "memory", "nodes": [], "edges": []}
        await sess.send(RpcResponse(request_id=req["request_id"], result=canned).model_dump_json())

        status, body = await get_task
        assert status == 200
        assert json.loads(body) == canned


async def test_rpc_timeout_returns_504(hub, monkeypatch):
    monkeypatch.setattr(hubmod, "RPC_TIMEOUT_SECONDS", 0.3)
    async with websockets.connect(hub.ingest) as sess:
        await _register(sess, _session("s-a"))
        await _wait(lambda: _pred_len(hub.addr, 1))

        # Never answer the RpcRequest → the endpoint must give up with 504.
        status, _ = await _http_get(hub.addr, "/api/graphs?session=s-a")
        assert status == 504


async def test_rpc_unknown_session_returns_404(hub):
    status, _ = await _http_get(hub.addr, "/api/graphs?session=nope")
    assert status == 404


# --- bind-race (run() port handling) ---


async def test_run_exits_0_when_a_healthy_hub_holds_the_port(hub, monkeypatch):
    host, port = hub.addr.split(":")
    monkeypatch.setenv("EPIMEMER_VIZ_HOST", host)
    monkeypatch.setenv("EPIMEMER_VIZ_PORT", port)
    # Port is held by the fixture's healthy hub → lost the spawn race → clean exit.
    # run() is blocking (bind attempt + health probe); off-load it so the event
    # loop keeps driving the fixture's hub to answer the probe.
    assert await asyncio.to_thread(hubmod.run) == 0


async def test_run_exits_1_when_a_stranger_holds_the_port(monkeypatch):
    stranger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stranger.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    stranger.bind(("127.0.0.1", 0))
    stranger.listen()
    port = stranger.getsockname()[1]
    try:
        monkeypatch.setenv("EPIMEMER_VIZ_HOST", "127.0.0.1")
        monkeypatch.setenv("EPIMEMER_VIZ_PORT", str(port))
        assert await asyncio.to_thread(hubmod.run) == 1
    finally:
        stranger.close()
