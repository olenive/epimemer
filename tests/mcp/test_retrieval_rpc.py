"""What the agent saw is what the frontend is told — through the real surface.

The feature's core invariant, asserted end to end rather than through
internals: a live hub, a live session client, real tool calls, and then the
records fetched over the `retrievals` RPC exactly as a browser fetches them
(`RETRIEVAL_PROVENANCE.md` §7).
"""

import asyncio
import json
import socket
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from fastmcp import FastMCP

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log, records_of
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.core.types import BASE_METACONTEXT_ID, Metacontext
from epimemer.storage.memory import InMemoryStorage
from epimemer.visualization.event_bus import create_event_bus
from epimemer.visualization.hub import create_hub_app
from epimemer.visualization.hub_client import start_hub_client
from epimemer.visualization.protocol import SessionInfo

def _graph_with_the_real() -> InMemoryStorage:
    """An in-memory graph somebody has set up.

    Since the frame requirement a frame is required at ingest and `the-real` is an ordinary
    metacontext, created once like any other frame.
    """
    store = InMemoryStorage()
    store._graphs[store._database].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID,
        content="The Real",
        description="Claims about the real world.",
    )
    return store

SESSION_ID = "session-under-test"


async def _http_get(addr: str, path: str) -> tuple[int, str]:
    def _do() -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"http://{addr}{path}", timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    return await asyncio.to_thread(_do)


async def _wait_for(pred, timeout: float = 3.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


@asynccontextmanager
async def _wired() -> AsyncIterator[SimpleNamespace]:
    """A hub, a session dialled into it, and an MCP server writing records."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    addr = f"127.0.0.1:{sock.getsockname()[1]}"

    server = uvicorn.Server(
        uvicorn.Config(create_hub_app(), log_level="warning", lifespan="off")
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started, "hub did not start"

    bus = create_event_bus()
    storage = _graph_with_the_real()
    log = new_record_log()
    stop_client = await start_hub_client(
        bus,
        storage,
        SessionInfo(
            session_id=SESSION_ID, pid=1, backend="memory", active_graph="default"
        ),
        f"ws://{addr}/ingest",
        records=lambda: [
            json.loads(record.model_dump_json()) for record in records_of(log)
        ],
    )

    @asynccontextmanager
    async def _lifespan(_server):
        yield {
            "storage": storage,
            "embedding_provider": MockEmbeddingProvider(
                model_id="mock-embed", dimension=8
            ),
            "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
            "event_bus": bus,
            "viz_session": None,
            "viz_hub_url": None,
            "retrievals": log,
        }

    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        try:
            async def _registered() -> bool:
                _, body = await _http_get(addr, "/api/sessions")
                return any(s["session_id"] == SESSION_ID for s in json.loads(body))

            await _wait_for(_registered)
            stopped = {"client": False}

            async def _stop_session() -> None:
                """End the MCP process's half without taking the hub with it."""
                if not stopped["client"]:
                    stopped["client"] = True
                    await stop_client()

            yield SimpleNamespace(
                server=epimemer_mcp, addr=addr, stop_session=_stop_session
            )
        finally:
            epimemer_mcp._lifespan_result = None
            epimemer_mcp._lifespan = original
            if not stopped["client"]:
                await stop_client()
            server.should_exit = True
            await serving


def _parse(result) -> dict:
    return json.loads(result.content[0].text)


async def _seed(server: FastMCP) -> list[str]:
    seg = _parse(await server.call_tool("segment", {"expected_graph": "default", 
        "content": (
            "The deployment rollback failed on Tuesday. "
            "The certificate rotation completed on Wednesday."
        ),
    }))["result"]
    await server.call_tool("store_decomposition", {"expected_graph": "default", 
        "metacontext_id": "the-real",
        "document_id": seg["document_id"],
        "segments": [{
            "segment_id": seg["segments"][0]["segment_id"],
            "topics": ["Deployment"],
            "facts": [
                "The deployment rollback failed on Tuesday",
                "The deployment rollback succeeded on Tuesday",
            ],
            "inferences": ["The release process is fragile"],
        }],
    })
    found = _parse(await server.call_tool(
        "find_nodes", {"expected_graph": "default", "sourced_from": seg["document_id"], "limit": 50}
    ))["result"]
    return [n["id"] for n in found["nodes"]]


async def test_rpc_hands_the_frontend_exactly_what_the_agent_saw():
    async with _wired() as wired:
        server, addr = wired.server, wired.addr
        known = await _seed(server)
        facts = [
            n["id"] for n in _parse(await server.call_tool(
                "find_nodes", {"expected_graph": "default", "sourced_from": known[0], "limit": 50}
            ))["result"]["nodes"]
        ] or known

        responses = {
            "epimemer.search": (await server.call_tool(
                "search", {"expected_graph": "default", "query": "deployment rollback", "k": 5}
            )).content[0].text,
            "epimemer.check_conflicts": (await server.call_tool(
                "check_conflicts", {"expected_graph": "default", "fact_ids": facts, "threshold": 0.0}
            )).content[0].text,
            "epimemer.reflect": (await server.call_tool(
                "reflect", {"expected_graph": "default", "similarity_threshold": 0.0}
            )).content[0].text,
        }

        status, body = await _http_get(addr, f"/api/retrievals?session={SESSION_ID}")

    assert status == 200
    records = {r["tool"]: r for r in json.loads(body)["records"]}

    for tool, text in responses.items():
        record = records[tool]
        declared = {node["node_id"] for node in record["retrieved"]}
        visible = {node_id for node_id in known if node_id in text}
        assert declared == visible, (
            f"{tool}: the frontend was told {sorted(declared)} and the agent "
            f"was shown {sorted(visible)}"
        )
        # And the payload came back with it — this route exists to carry it.
        assert record["response_text"] == text


async def test_a_reflect_record_names_its_nominees_not_its_scan():
    """§2's semantics, pinned where it would be easiest to get wrong.

    `reflect` walks the whole active graph and the agent sees only the
    nominees, so a reflect record dims everything except them. That is
    accurate — `retrieved` is what the response carried, never what the tool
    looked at — and no special case.
    """
    async with _wired() as wired:
        server, addr = wired.server, wired.addr
        known = await _seed(server)
        text = (await server.call_tool(
            "reflect", {"expected_graph": "default", "similarity_threshold": 0.99}
        )).content[0].text

        status, body = await _http_get(addr, f"/api/retrievals?session={SESSION_ID}")

    assert status == 200
    record = [r for r in json.loads(body)["records"] if r["tool"] == "epimemer.reflect"][-1]
    declared = {node["node_id"] for node in record["retrieved"]}

    assert declared == {node_id for node_id in known if node_id in text}
    assert declared < set(known), "a scan of the whole graph is not a retrieval of it"


async def test_the_rpc_is_gone_with_the_session_and_the_mirror_is_not():
    """The two routes are complementary, which is the whole of §3.2's revision.

    The payload route dies with the process that produced it; the hub's mirror
    is what is still there when you open the dashboard afterwards.
    """
    async with _wired() as wired:
        server, addr = wired.server, wired.addr
        await _seed(server)
        await server.call_tool("search", {"expected_graph": "default", "query": "deployment", "k": 5})
        live_status, _ = await _http_get(addr, f"/api/retrievals?session={SESSION_ID}")
        assert live_status == 200

        await wired.stop_session()

        async def _disconnected() -> bool:
            _, body = await _http_get(addr, "/api/sessions")
            return not any(s["connected"] for s in json.loads(body))

        await _wait_for(_disconnected)
        status, _ = await _http_get(addr, f"/api/retrievals?session={SESSION_ID}")

    assert status in (404, 502, 504)
