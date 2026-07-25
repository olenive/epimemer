"""Tests for the `viz_status` MCP tool.

`viz_status` answers "where is this session publishing, and can the hub see it?"
from inside the very process being driven, so it is the durable fix for "I opened
the visualizer but can't find my graph".
"""

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest
import uvicorn
import websockets

from epimemer.mcp.config import ServerConfig
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.hub import create_hub_app
from epimemer.visualization.protocol import Register, SessionInfo


def _result(tool_result) -> dict:
    return json.loads(tool_result.content[0].text)["result"]


def _deps(viz_session=None, viz_hub_url=None) -> dict:
    return {
        "storage": InMemoryStorage(),
        "embedding_provider": None,
        "config": ServerConfig(),
        "viz_session": viz_session,
        "viz_hub_url": viz_hub_url,
    }


@asynccontextmanager
async def _server_with(deps: dict):
    original = epimemer_mcp._lifespan

    @asynccontextmanager
    async def _lifespan(_server):
        yield deps

    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        try:
            yield epimemer_mcp
        finally:
            epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


async def test_viz_status_disabled_reports_not_enabled():
    async with _server_with(_deps()) as server:
        res = _result(await server.call_tool("viz_status", {}))
        assert res == {"viz_enabled": False}


async def test_viz_status_reports_unreachable_hub():
    session = SessionInfo(session_id="sess-1", pid=1, backend="memory", active_graph="default")
    # Point at a dead port — nothing answers.
    async with _server_with(_deps(session, "http://127.0.0.1:1")) as server:
        res = _result(await server.call_tool("viz_status", {}))
        assert res["viz_enabled"] is True
        assert res["hub_reachable"] is False
        assert res["connected"] is False
        assert res["sessions_on_hub"] == 0
        assert res["session_id"] == "sess-1"
        assert res["backend"] == "memory"
        assert res["active_graph"] == "default"


async def test_viz_status_connected_with_live_hub():
    app = create_hub_app()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    hub = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    hub_task = asyncio.create_task(hub.serve(sockets=[sock]))
    for _ in range(300):
        if hub.started:
            break
        await asyncio.sleep(0.01)
    assert hub.started

    addr = f"127.0.0.1:{port}"
    session = SessionInfo(session_id="sess-live", pid=1, backend="memory", active_graph="default")
    try:
        # Register a matching session on the hub over its ingest socket.
        async with websockets.connect(f"ws://{addr}/ingest") as ingest:
            await ingest.send(Register(info=session).model_dump_json())
            await asyncio.sleep(0.2)  # let the hub record the registration

            async with _server_with(_deps(session, f"http://{addr}")) as server:
                res = _result(await server.call_tool("viz_status", {}))
                assert res["viz_enabled"] is True
                assert res["hub_reachable"] is True
                assert res["connected"] is True
                assert res["sessions_on_hub"] >= 1
                assert res["session_id"] == "sess-live"
    finally:
        hub.should_exit = True
        await hub_task
