"""Tests for the session-side hub client.

Each test runs a throwaway ``websockets`` server as a stand-in hub, so we can
observe exactly what the client sends (Register / PublishEvent / RpcResponse)
and drive RPC requests back at it.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve

from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import InProcessEventBus
from epimemer.visualization.events import NodeStatusChanged
from epimemer.visualization.hub_client import start_hub_client
from epimemer.visualization.protocol import RpcRequest, SessionInfo


def _info() -> SessionInfo:
    return SessionInfo(session_id="s1", pid=1, backend="memory", active_graph="default")


async def _make_fake_hub(port: int = 0):
    received: asyncio.Queue = asyncio.Queue()
    state: dict = {"ws": None, "connections": 0}

    async def handler(ws) -> None:
        state["connections"] += 1
        state["ws"] = ws
        try:
            async for raw in ws:
                await received.put(json.loads(raw))
        except Exception:
            pass

    server = await serve(handler, "127.0.0.1", port)
    bound_port = server.sockets[0].getsockname()[1]
    return SimpleNamespace(
        server=server,
        received=received,
        state=state,
        port=bound_port,
        url=f"ws://127.0.0.1:{bound_port}/ingest",
    )


async def _close(hub) -> None:
    hub.server.close()
    await hub.server.wait_closed()


async def test_client_registers_forwards_event_and_answers_rpc():
    hub = await _make_fake_hub()
    storage = InMemoryStorage()
    bus = InProcessEventBus()
    stop = await start_hub_client(bus, storage, _info(), hub.url)
    try:
        first = await asyncio.wait_for(hub.received.get(), timeout=3)
        assert first["type"] == "register"
        assert first["info"]["session_id"] == "s1"
        assert first["info"]["backend"] == "memory"

        # A bus event is forwarded verbatim as a PublishEvent.
        await bus.publish(
            NodeStatusChanged(graph="g1", node_id="n1", old_status="active", new_status="superseded")
        )
        fwd = await asyncio.wait_for(hub.received.get(), timeout=3)
        assert fwd["type"] == "event"
        assert fwd["payload"]["event_type"] == "node_status_changed"
        assert fwd["payload"]["node_id"] == "n1"

        # A list_graphs RPC is answered from this process's own storage.
        await hub.state["ws"].send(
            RpcRequest(request_id="r1", method="list_graphs").model_dump_json()
        )
        resp = await asyncio.wait_for(hub.received.get(), timeout=3)
        assert resp["type"] == "rpc_response"
        assert resp["request_id"] == "r1"
        assert resp["result"]["backend"] == "memory"
        assert isinstance(resp["result"]["graphs"], list)
        assert "active_graph" in resp["result"]
    finally:
        await stop()
        await _close(hub)


async def test_client_reconnects_and_reregisters():
    hub1 = await _make_fake_hub()
    port = hub1.port
    stop = await start_hub_client(InProcessEventBus(), InMemoryStorage(), _info(), hub1.url)
    try:
        first = await asyncio.wait_for(hub1.received.get(), timeout=3)
        assert first["type"] == "register"

        # Drop the hub, then bring a fresh one up on the same port.
        await _close(hub1)
        await asyncio.sleep(0.2)
        hub2 = await _make_fake_hub(port=port)
        try:
            # Client should reconnect (capped backoff) and register again.
            reg = await asyncio.wait_for(hub2.received.get(), timeout=8)
            assert reg["type"] == "register"
            assert reg["info"]["session_id"] == "s1"
        finally:
            await _close(hub2)
    finally:
        await stop()


async def test_unreachable_hub_logs_to_stderr_once(capsys):
    # Nothing listens on port 1 → connection refused on every attempt.
    stop = await start_hub_client(
        InProcessEventBus(), InMemoryStorage(), _info(), "ws://127.0.0.1:1/ingest"
    )
    # Long enough for at least two retry attempts (backoff 1s then 2s).
    await asyncio.sleep(2.5)
    await stop()

    err = capsys.readouterr().err
    # The one-time stderr note is emitted exactly once; later retries are debug.
    assert err.count("[epimemer] viz hub unreachable") == 1
